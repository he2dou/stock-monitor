from __future__ import annotations

import uvicorn

from src.web.app import create_app


def main() -> None:
    app = create_app()
    cfg = (app.state.app_config.get("web", {}) or {})
    host = cfg.get("host", "127.0.0.1")
    port = int(cfg.get("port", 8000))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
