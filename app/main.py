import os
import uvicorn
from fastapi import FastAPI

app = FastAPI(
    title="EBReady",
    description="EBReady - Deploying applications to AWS Elastic Beanstalk made simple",
    version="1.0.0",
)

# Alias for WSGI/ASGI servers expecting 'application'
application = app


@app.get("/")
def get_root():
    return {
        "service": "EBReady",
        "message": "EBReady is running on AWS Elastic Beanstalk",
    }


@app.get("/health")
def get_health():
    return {
        "status": "ok",
        "service": "ebready",
    }


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, log_level="info")
