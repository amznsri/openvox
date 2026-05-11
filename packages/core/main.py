"""OpenVox core entry point.

Starts the FastAPI app with uvicorn. In production we pass the app
object directly so uvicorn doesn't need an import string (avoids the
classic "must be import string with reload" trap).
"""

from __future__ import annotations

import uvicorn

from openvox.api.app import create_app
from openvox.config import get_settings


def main() -> None:
    settings = get_settings()
    app = create_app()
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.core_port,
        log_level=settings.log_level,
        ws="websockets",
        # No reload in container — file-watching reload doesn't play well
        # with uvicorn's import-string requirement and we always rebuild on
        # code changes anyway.
        reload=False,
    )


if __name__ == "__main__":
    main()
