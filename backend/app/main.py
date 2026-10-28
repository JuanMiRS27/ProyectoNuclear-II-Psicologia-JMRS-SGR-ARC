from fastapi import FastAPI

app = FastAPI(
    title="Simulador de Psicología Social",
    description="API del Proyecto Nuclear II",
    version="0.1.0",
)


@app.get("/health", tags=["sistema"])
def health() -> dict[str, str]:
    return {"estado": "ok"}
