"""Keep localhost companion tests independent from runner proxy settings."""

import os


# httpx reads both upper- and lower-case proxy variables depending on the
# platform.  Tests exercise only loopback providers, so never route them
# through a CI or developer-machine proxy.
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"
