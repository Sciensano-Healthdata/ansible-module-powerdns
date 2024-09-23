#!/usr/bin/env python
# -*- coding: utf-8 -*-

DOCUMENTATION = '''
---
module: powerdns_record
short_description: Manage PowerDNS records
description:
- Create, update or delete a PowerDNS records using API
- A record is unique identified by name and type
- Changing a records type is therefore not possible
options:
  content:
    description:
    - Content of the record
    - Could be an ip address or hostname
    - Can be specified as list for multiple records of the same type and name
    - Mandatory when deleting a record and exclusive is disabled
  exclusive:
    description:
      - Whether to remove all other non-specified records from the recordset.
        Applicable to A, AAAA, MX, SRV, and TXT records.
    required: false
    default: true
  name:
    description:
    - Record name
    - If name is not an FQDN, zone will be added at the end to create an FQDN
    required: true
  server:
    description:
    - Server name.
    required: false
    default: localhost
  ttl:
    description:
    - Record TTL
    required: false
    default: 86400
  type:
    description:
    - Record type
    required: false
    choices: ['A', 'AAAA', 'CNAME', 'MX', 'PTR', 'SOA', 'SRV', 'TXT', 'LUA', 'NS', 'SSHFP']
    default: None
  set_ptr:
    description:
    - >
      If set to true, the server will find the matching reverse zone and create
      a PTR there. Existing PTR records are replaced. If no matching reverse
      Zone, an error is thrown. Only valid in client bodies, only valid for A
      and AAAA types.
    required: false
    choices: ['True', 'False']
    default: False
  zone:
    description:
    - Name of zone where to ensure the record
    required: true
  pdns_host:
    description:
    - Name or ip address of PowerDNS host
    required: false
    default: 127.0.0.1
  pdns_port:
    description:
    - Port used by PowerDNS API
    required: false
    default: 8081
  pdns_prot:
    description:
    - Protocol used by PowerDNS API
    required: false
    default: http
    choices: ['http', 'https']
  pdns_api_key:
    description:
    - API Key to authenticate through PowerDNS API
  pdns_api_username:
    description:
    - API Username to authenticate through PowerDNS API with basic auth
  pdns_api_password:
    description:
    - API Password to authenticate through PowerDNS API with basic auth
  strict_ssl_checking:
    description:
    - Disables strict certificate checking
    default: true
author: "Thomas Krahn (@nosmoht)"
'''

EXAMPLES = '''
- powerdns_record:
    name: host01.internal.example.com
    type: A
    content: 192.168.1.234
    state: present
    zone: internal.example.com
    pdns_host: powerdns.example.com
    pdns_port: 8080
    pdns_prot: http
    pdns_api_key: topsecret
- powerdns_record:
    name: example.com
    type: NS
    content:
      - ns1.example.com
      - ns2.example.com
    state: present
    zone: example.com
    pdns_host: powerdns.example.com
    pdns_port: 8080
    pdns_prot: http
    pdns_api_key: topsecret
'''

try:
    import requests
    from requests.auth import HTTPBasicAuth
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class PowerDNSError(Exception):
    def __init__(self, url, status_code, message):
        super().__init__(url, status_code, message)
        self.url = url
        self.status_code = status_code
        self.message = message

class PowerDNSClient:
    def __init__(self, host, port, prot, api_key, api_username, api_password, verify):
        self.url = '{prot}://{host}:{port}/api/v1'.format(prot=prot, host=host, port=port)
        self.session = requests.Session()
        if api_key:
            self.session.headers.update({'X-API-Key': api_key})
        elif (api_username and api_password):
            self.session.auth = HTTPBasicAuth(api_username, api_password)
        self.session.verify = verify

    def _handle_request(self, req):
        if req.status_code in [200, 201, 204]:
            if req.text:
                try:
                    return req.json()
                except Exception as e:
                    print(e) # same as yield
            return dict()
        elif req.status_code == 404:
            error_message = 'Not found'
        else:
            error_message = self._get_request_error_message(data=req)

        raise PowerDNSError(url=req.url,
                            status_code=req.status_code,
                            message=error_message)

    def _get_request_error_message(self, data):
        try:
            request_json = data.json()
            request_error = None
            if 'error' in request_json:
                request_error = request_json.get('error')
            elif 'errors' in request_json:
                request_error = request_json.get('errors')
            elif 'msg' in request_json:
                request_error = request_json.get('msg')
            return request_error
        except Exception:
          pass
        return data.text

    def _get_search_url(self, server):
        return '{url}/servers/{server}/search-data'.format(url=self.url,
                                                           server=server)
    def _get_zones_url(self, server):
        return '{url}/servers/{server}/zones'.format(url=self.url, server=server)

    def _get_zone_url(self, server, name):
        return '{url}/{name}'.format(url=self._get_zones_url(server), name=name)

    @staticmethod
    def _make_canonical(name):
        if not name.endswith('.'):
            name += '.'

        return name

    def get_record(self, server, zone, name, rtype):
        """Search for a given record (name) in the specified zone."""
        url = self._get_zone_url(server, zone)

        resp = self._handle_request(self.session.get(url=url))

        # Canonicalize record name and zone
        canonical_name = self._make_canonical(name)
        rrsets = resp['rrsets']
        rrset = next((r for r in rrsets if r['name'] == canonical_name and r['type'] == rtype), dict(records=[], comments=[]))

        return rrset

    def _get_request_data(self, changetype, server, zone, name, rtype, set_ptr=False, content=None, disabled=None, ttl=None):
        record_content = list()
        if content:
            for record in content:
                entry = dict(content=record, disabled=disabled)
                if rtype in ['A', 'AAAA'] and set_ptr:
                    entry['set-ptr'] = True

                record_content.append(entry)

        record = dict(name=name, type=rtype, changetype=changetype, records=record_content, ttl=ttl)
        rrsets = list()
        rrsets.append(record)
        data = dict(rrsets=rrsets)
        return data

    def create_record(self, server, zone, name, rtype, content, disabled, ttl, set_ptr):
        url = self._get_zone_url(server=server, name=zone)

        # Ensure record name is fully canonical
        canonical_name = self._make_canonical(name)

        data = self._get_request_data(
            changetype='REPLACE',
            server=server,
            zone=zone,
            name=canonical_name,
            rtype=rtype,
            content=content,
            set_ptr=set_ptr,
            disabled=disabled,
            ttl=ttl
        )
        req = self.session.patch(url=url, json=data)
        return self._handle_request(req)

    def delete_record(self, server, zone, name, rtype):
        canonical_name = self._make_canonical(name)
        url = self._get_zone_url(server=server, name=zone)
        data = self._get_request_data(changetype='DELETE', server=server,
                                      zone=zone, name=canonical_name, rtype=rtype)
        req = self.session.patch(url=url, json=data)
        return self._handle_request(req)


def serial(content):
    """ Returns the serial of the given SOA record. """
    return content.split(' ')[2]


def ignore_serial(content):
    """ Returns the SOA record with the serial removed. """

    parts = content.split(' ')
    return ' '.join(parts[:2] + parts[3:])


def matches_existing_content(rtype, content, existing_content):
    """ Returns True if the content of the given record matches the existing
    content (i.e. if no change is necessary).

    """

    # Ignore the serial in SOA records if it is 0, which signifies a serial
    # that is automatically incremented (as often happens in PowerDNS setups).
    if rtype == 'SOA' and serial(content) == '0':
        content = ignore_serial(content)
        existing_content = [ignore_serial(e) for e in existing_content]

    return content in existing_content


def ensure(module, pdns_client):
    content = module.params['content']
    disabled = module.params['disabled']
    exclusive = module.params['exclusive']
    name = module.params['name']
    rtype = module.params['type']
    set_ptr = module.params['set_ptr']
    ttl = module.params['ttl']
    zone_name = module.params['zone']
    server = module.params['server']
    state = module.params['state']

    # Remove trailing periods on records
    # Will be added later during CRUD operations.
    if name.endswith('.'):
        name = name.rstrip('.')
    if zone_name.endswith('.'):
        zone_name = zone_name.rstrip('.')

    if zone_name not in name:
        name = '{name}.{zone}'.format(name=name, zone=zone_name)

    # Try to find the record by name and type
    record = pdns_client.get_record(name=name, server=server, rtype=rtype, zone=zone_name)
    existing_content = [c.get('content') for c in record["records"]]

    # Sanitize user-provided input for certain record types
    if content:
        # Lowercase IPv6 addresses to match case returned by the API.
        # Necessary for later comparisons.
        if rtype == 'AAAA':
            content_sanitized = list()
            for item in content:
                content_sanitized.append(item.lower())
            content = content_sanitized

        # Ensure TXT records are double quoted
        if rtype == 'TXT':
            for itemid, item in enumerate(content):
                if not (item.startswith('"') and item.endswith('"')):
                    content[itemid] = '"{}"'.format(item.strip('"'))

    if state == 'present':
        record_content = []

        # Create record if it does not exist
        if not existing_content:
            record_content = content

            try:
                if not module.check_mode:
                    pdns_client.create_record(server=server, zone=zone_name, name=name, rtype=rtype, content=record_content,
                                              set_ptr=set_ptr, ttl=ttl, disabled=disabled)
                return True, pdns_client.get_record(server=server, rtype=rtype, zone=zone_name, name=name)
            except PowerDNSError as e:
                module.fail_json(
                        msg='Could not create record {name}: HTTP {code}: {err}'.format(name=name, code=e.status_code,
                                                                                        err=e.message))
        # Check if changeable parameters match, else update record.
        for item in content:
            if not matches_existing_content(rtype, item, existing_content) or record.get('ttl', None) != ttl:
                # Add provided content to record content payload
                record_content.append(item)

        # If the existing content includes all items in the content input
        if len(set(existing_content)) > len(set(content)) and exclusive:
            record_content = content

        if len(record_content) > 0:
            print(len(record_content))
            # Add items that are both in the content input and existing content
            record_content = set(record_content + content)
            # Add existing content to payload if not exclusive
            if not exclusive:
                record_content = existing_content + [item for item in record_content if item not in existing_content]

            try:
                if not module.check_mode:
                    pdns_client.create_record(server=server, zone=zone_name, name=name, rtype=rtype, content=record_content,
                                              set_ptr=set_ptr, ttl=ttl, disabled=disabled)
                return True, pdns_client.get_record(server=server, rtype=rtype, zone=zone_name, name=name)
            except PowerDNSError as e:
                module.fail_json(
                        msg='Could not update record {name}: HTTP {code}: {err}'.format(name=name, code=e.status_code,
                                                                                        err=e.message))
    elif state == 'absent':
        if existing_content and exclusive:
            # Delete entire record
            try:
                if not module.check_mode:
                    pdns_client.delete_record(server=server, zone=zone_name, name=name, rtype=rtype)
                return True, None
            except PowerDNSError as e:
                module.fail_json(
                        msg='Could not delete record {name}: HTTP {code}: {err}'.format(name=name, code=e.status_code,
                                                                                        err=e.message))
        elif existing_content and not exclusive:
            # Remove specified record from the recordset. Update record.
            record_content = [item for item in existing_content if item not in content]
            if len(record_content) != len(existing_content):
                try:
                    if not module.check_mode:
                        pdns_client.create_record(server=server, zone=zone_name, name=name, rtype=rtype, content=record_content,
                                                  set_ptr=set_ptr, ttl=ttl, disabled=disabled)
                    return True, pdns_client.get_record(server=server, rtype=rtype, zone=zone_name, name=name)
                except PowerDNSError as e:
                    module.fail_json(
                            msg='Could not delete record {name}: HTTP {code}: {err}'.format(name=name, code=e.status_code,
                                                                                            err=e.message))
                try:
                    if not module.check_mode:
                        pdns_client.delete_record(server=server, zone=zone_name, name=name, rtype=rtype)
                    return True, None
                except PowerDNSError as e:
                    module.fail_json(
                            msg='Could not delete record {name}: HTTP {code}: {err}'.format(name=name, code=e.status_code,
                                                                                            err=e.message))

    return False, record


def main():
    module = AnsibleModule(
            argument_spec=dict(
                    content=dict(type='list', required=False),
                    exclusive=dict(type='bool', default=True),
                    disabled=dict(type='bool', default=False),
                    name=dict(type='str', required=True),
                    server=dict(type='str', default='localhost'),
                    set_ptr=dict(type='bool', default=False),
                    state=dict(type='str', default='present', choices=['present', 'absent']),
                    ttl=dict(type='int', default=86400),
                    type=dict(type='str', required=False, choices=['A', 'AAAA', 'CNAME', 'MX', 'PTR', 'SOA', 'SRV', 'TXT', 'LUA', 'NS', 'SSHFP']),
                    zone=dict(type='str', required=True),
                    pdns_host=dict(type='str', default='127.0.0.1'),
                    pdns_port=dict(type='int', default=8081),
                    pdns_prot=dict(type='str', default='http', choices=['http', 'https']),
                    pdns_api_key=dict(type='str', required=False, no_log=True),
                    pdns_api_username=dict(type='str', required=False),
                    pdns_api_password=dict(type='str', required=False, no_log=True),
                    strict_ssl_checking=dict(type='bool', default=True),
            ),
            supports_check_mode=True,
    )

    if not HAS_REQUESTS:
        module.fail_json(msg="requests must be installed to use this module.")

    pdns_client = PowerDNSClient(host=module.params['pdns_host'],
                                 port=module.params['pdns_port'],
                                 prot=module.params['pdns_prot'],
                                 api_key=module.params['pdns_api_key'],
                                 api_username=module.params['pdns_api_username'],
                                 api_password=module.params['pdns_api_password'],
                                 verify=module.params['strict_ssl_checking'])

    try:
        changed, record = ensure(module, pdns_client)
        module.exit_json(changed=changed, record=record)
    except Exception as e:
        module.fail_json(msg='Error: {0}'.format(str(e)))


# import module snippets
from ansible.module_utils.basic import *

if __name__ == '__main__':
    main()
