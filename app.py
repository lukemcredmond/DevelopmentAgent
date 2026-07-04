from backend.bootstrap import initialize
from backend.main import app

if __name__ == "__main__":
    initialize()
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=6767)
