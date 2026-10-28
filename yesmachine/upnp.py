"""Small IGD client with bounded SSDP discovery and owned mapping lifecycle."""

import ipaddress
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from xml.sax.saxutils import escape


class GatewayError(RuntimeError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def text_fields(xml):
    return {element.tag.split("}")[-1]: (element.text or "") for element in ET.fromstring(xml).iter()}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GatewayError("The router redirected its UPnP endpoint. Mapping was not changed.")


def fetch(url, body=None, headers=None):
    request = urllib.request.Request(url, data=body, headers=headers or {})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=4) as response:
            result = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        result = error.read(65536)
        try:
            fields = text_fields(result)
            code = int(fields.get("errorCode", "0"))
            raise GatewayError(fields.get("errorDescription") or f"Router returned HTTP {error.code}", code)
        except (ET.ParseError, ValueError):
            raise GatewayError(f"Router returned HTTP {error.code}.") from None
    if len(result) > 1024 * 1024:
        raise GatewayError("The router returned an oversized UPnP response.")
    return result


def check_router_url(url, responder):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
        raise GatewayError("Invalid router control address.")
    if parsed.hostname != responder:
        raise GatewayError("The router advertised a control address on a different computer.")
    address = ipaddress.ip_address(responder)
    if not address.is_private or address.is_loopback or address.is_link_local:
        raise GatewayError("UPnP discovery did not come from a private-network router.")


@dataclass
class Gateway:
    control_url: str
    service: str
    local_ip: str
    name: str = "UPnP router"

    def action(self, action, **arguments):
        fields = "".join(f"<{key}>{escape(str(value))}</{key}>" for key, value in arguments.items())
        payload = ('<?xml version="1.0"?>'
                   '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                   's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
                   f'<s:Body><u:{action} xmlns:u="{self.service}">{fields}'
                   f'</u:{action}></s:Body></s:Envelope>').encode()
        response = fetch(self.control_url, payload, {
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{self.service}#{action}"',
        })
        fields = text_fields(response)
        if "errorCode" in fields:
            raise GatewayError(fields.get("errorDescription", "Router rejected the request."), int(fields["errorCode"]))
        return fields

    def external_ip(self):
        value = self.action("GetExternalIPAddress").get("NewExternalIPAddress", "")
        try:
            ipaddress.ip_address(value)
        except ValueError:
            raise GatewayError("The router did not return a valid external address.") from None
        return value

    def mapping(self, port):
        try:
            result = self.action("GetSpecificPortMappingEntry", NewRemoteHost="",
                                 NewExternalPort=port, NewProtocol="TCP")
            # Some Netgear IGDs return an empty SOAP body with HTTP 200 for
            # a missing entry rather than the standard 714 fault. Add still
            # requires an explicit ownership confirmation afterward.
            if not any(key in result for key in ("NewInternalClient", "NewInternalPort", "NewPortMappingDescription")):
                return None
            return result
        except GatewayError as error:
            if error.code == 714:
                return None
            raise

    def owned(self, mapping, marker, port, internal_port=None):
        return bool(mapping and mapping.get("NewPortMappingDescription") == marker
                    and mapping.get("NewInternalClient") == self.local_ip
                    and str(mapping.get("NewInternalPort")) == str(internal_port or port))

    def add(self, port, marker, lease=3600, internal_port=None):
        internal_port = internal_port or port
        current = self.mapping(port)
        if current and not self.owned(current, marker, port, internal_port):
            raise GatewayError(f"TCP port {port} already belongs to another mapping. It was left unchanged.")
        arguments = dict(NewRemoteHost="", NewExternalPort=port, NewProtocol="TCP",
                         NewInternalPort=internal_port, NewInternalClient=self.local_ip,
                         NewEnabled=1, NewPortMappingDescription=marker, NewLeaseDuration=lease)
        try:
            self.action("AddPortMapping", **arguments)
        except GatewayError as error:
            if error.code != 725:
                raise
            arguments["NewLeaseDuration"] = 0
            self.action("AddPortMapping", **arguments)
            lease = 0
        check = self.mapping(port)
        if not self.owned(check, marker, port, internal_port):
            raise GatewayError("The router did not confirm YesMachine's port mapping.")
        return lease

    def remove(self, port, marker, internal_port=None):
        current = self.mapping(port)
        if not current:
            return
        if not self.owned(current, marker, port, internal_port):
            raise GatewayError("The mapping is no longer owned by this installation. It was left unchanged.")
        self.action("DeletePortMapping", NewRemoteHost="", NewExternalPort=port, NewProtocol="TCP")
        if self.mapping(port):
            raise GatewayError("The router still reports the mapping. Retry removal before quitting.")


def discover(timeout=3):
    packet = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
              'MAN: "ssdp:discover"\r\nMX: 2\r\n'
              "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n").encode()
    locations = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.bind(("", 0))
        sock.sendto(packet, ("239.255.255.250", 1900))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and len(locations) < 16:
            sock.settimeout(max(.01, deadline - time.monotonic()))
            try:
                data, peer = sock.recvfrom(65536)
            except socket.timeout:
                break
            headers = {}
            for line in data.decode("utf-8", errors="replace").splitlines()[1:]:
                key, separator, value = line.partition(":")
                if separator:
                    headers[key.lower().strip()] = value.strip()
            location = headers.get("location")
            if location and (location, peer[0]) not in locations:
                locations.append((location, peer[0]))
    errors = []
    for location, responder in locations:
        try:
            check_router_url(location, responder)
            root = ET.fromstring(fetch(location))
            name = next((x.text for x in root.iter() if x.tag.endswith("}friendlyName")), "UPnP router")
            for service in root.iter():
                if service.tag.split("}")[-1] != "service":
                    continue
                values = {x.tag.split("}")[-1]: x.text or "" for x in service}
                kind = values.get("serviceType", "")
                if kind in ("urn:schemas-upnp-org:service:WANIPConnection:1",
                            "urn:schemas-upnp-org:service:WANIPConnection:2",
                            "urn:schemas-upnp-org:service:WANPPPConnection:1"):
                    control = urllib.parse.urljoin(location, values["controlURL"])
                    check_router_url(control, responder)
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as local:
                        local.connect((responder, 1900))
                        address = local.getsockname()[0]
                    return Gateway(control, kind, address, name or "UPnP router")
        except (OSError, ValueError, ET.ParseError, GatewayError) as error:
            errors.append(str(error))
    detail = " " + errors[0] if errors else " Enable UPnP on your router or use a LAN address."
    raise GatewayError("No compatible UPnP router was found." + detail)
