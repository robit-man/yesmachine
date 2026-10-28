import unittest

from yesmachine.upnp import Gateway, GatewayError, check_router_url


class FakeGateway(Gateway):
    def __init__(self):
        super().__init__("http://192.168.1.1/control", "urn:test", "192.168.1.39")
        self.current = None
        self.calls = []
        self.permanent_only = False

    def action(self, action, **arguments):
        self.calls.append((action, arguments))
        if action == "GetSpecificPortMappingEntry":
            if self.current is None:
                raise GatewayError("NoSuchEntry",714)
            return self.current
        if action == "AddPortMapping":
            if self.permanent_only and arguments["NewLeaseDuration"]:
                raise GatewayError("OnlyPermanentLeasesSupported",725)
            self.current = {key:arguments[key] for key in ["NewPortMappingDescription","NewInternalClient","NewInternalPort"]}
            return {}
        if action == "DeletePortMapping":
            self.current = None
            return {}
        return {}


class MappingTests(unittest.TestCase):
    def test_create_renew_and_remove_only_owned_mapping(self):
        gateway = FakeGateway()
        self.assertEqual(gateway.add(21180,"YesMachine-test"),3600)
        self.assertEqual(gateway.add(21180,"YesMachine-test"),3600)
        gateway.remove(21180,"YesMachine-test")
        self.assertIsNone(gateway.current)

    def test_foreign_mapping_is_never_overwritten_or_deleted(self):
        gateway = FakeGateway()
        gateway.add(21180,"NoMachine")
        gateway.calls.clear()
        with self.assertRaisesRegex(GatewayError,"already belongs"):
            gateway.add(21180,"YesMachine-test")
        with self.assertRaisesRegex(GatewayError,"no longer owned"):
            gateway.remove(21180,"YesMachine-test")
        self.assertFalse(any(action in ["AddPortMapping","DeletePortMapping"] for action,_ in gateway.calls))

    def test_marker_on_another_machine_is_not_ownership(self):
        gateway = FakeGateway()
        gateway.add(21180,"YesMachine-test")
        gateway.current["NewInternalClient"]="192.168.1.100"
        with self.assertRaises(GatewayError):
            gateway.remove(21180,"YesMachine-test")

    def test_permanent_lease_fallback_is_explicit(self):
        gateway = FakeGateway()
        gateway.permanent_only = True
        self.assertEqual(gateway.add(21180,"YesMachine-test"),0)
        gateway.remove(21180,"YesMachine-test")
        self.assertIsNone(gateway.current)

    def test_discovery_cannot_redirect_to_unrelated_or_metadata_host(self):
        for url, responder in [("http://169.254.169.254/metadata","192.168.1.1"),
                               ("file:///etc/passwd","192.168.1.1"),
                               ("http://127.0.0.1/control","127.0.0.1"),
                               ("http://user:password@192.168.1.1/control","192.168.1.1"),
                               ("http://8.8.8.8/control","8.8.8.8")]:
            with self.subTest(url=url),self.assertRaises(GatewayError):
                check_router_url(url,responder)

    def test_valid_private_router_endpoint(self):
        check_router_url("http://192.168.1.1:5000/control","192.168.1.1")

    def test_empty_netgear_response_means_missing_entry(self):
        gateway=Gateway("http://192.168.1.1/control","urn:test","192.168.1.39")
        gateway.action=lambda *_args,**_kwargs:{"Envelope":"\n","Body":"\n"}
        self.assertIsNone(gateway.mapping(21180))
