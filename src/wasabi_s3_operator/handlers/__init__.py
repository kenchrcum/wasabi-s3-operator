"""Handler modules for CRD resources."""

# Import handlers to register them - all handlers register themselves via @kopf decorators
from . import access_key  # noqa: F401
from . import bucket  # noqa: F401
from . import bucket_policy  # noqa: F401
from . import iampolicy  # noqa: F401
from . import provider  # noqa: F401
from . import user  # noqa: F401

