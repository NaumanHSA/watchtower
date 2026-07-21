from app.app import create_app
from config import config
import multiprocessing
import uvicorn

app = create_app()
    

if __name__ == "__main__":
    try:
        multiprocessing.set_start_method("spawn")
    except RuntimeError:
        pass
    uvicorn.run(
        app,
        host=config.HOST,
        port=config.HOST_PORT,
        reload=False,
        log_level=config.LOGS_LEVEL.lower(),
        workers=1,
    )
