from app.app import create_app
from config import config

app = create_app()

if __name__ == "__main__":
    from uvicorn import run
    run(app, host=config.HOST, port=config.PORT, log_level=config.LOG_LEVEL.lower())