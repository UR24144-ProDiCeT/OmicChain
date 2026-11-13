import sys, importlib.util, os
from requests import Response
spec = importlib.util.find_spec("rocrate")
LOCAL = open(os.path.join(os.path.dirname(spec.origin), "data", "ro-crate.jsonld"), "rb").read()
TARGETS = {"https://w3id.org/ro/crate/1.1/context", "https://w3id.org/ro/crate/1.1/context/"}
from rocrate_validator.utils.http import HttpRequester
_orig_getattr = HttpRequester.__getattr__
def get(self, url, **kw):
    if url in TARGETS:
        r = Response(); r.status_code = 200; r._content = LOCAL
        r.headers["Content-Type"] = "application/ld+json"; r.url = url
        return r
    return _orig_getattr(self, "get")(url, **kw)
HttpRequester.get = get
from rocrate_validator.cli.main import cli
sys.argv = ["rocrate-validator"] + sys.argv[1:]
cli()
