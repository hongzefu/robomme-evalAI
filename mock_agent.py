import random

from fastapi import FastAPI


app = FastAPI(title="MiniGrid Mock Agent")


@app.post("/act")
def act():
    return {"action": random.randint(0, 6)}
