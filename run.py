import os
from pathlib import Path
from dotenv import load_dotenv
import uvicorn

if __name__ == "__main__":
    load_dotenv(Path(__file__).parent / ".env")
    uvicorn.run("app.main:app", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8871")), workers=1, proxy_headers=False)
