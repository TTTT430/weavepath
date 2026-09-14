"""Keep localhost companion tests independent from runner proxy settings."""

import os


# httpx reads both upper- and lower-case proxy variables depending on the
# platform. Tests exercise only loopback providers, so never route them
# through a CI or developer-machine proxy.
for _name in (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
):
    os.environ.pop(_name, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
