import os, uuid, httpx, uvicorn
from pathlib import Path
from mcp.server.fastmcp import FastMCP, Context
from starlette.responses import FileResponse, PlainTextResponse

PORT = int(os.environ.get("PORT", "8000"))
MODEL = os.environ.get("FISH_MODEL", "s1")
BASE = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000")
OUT = Path("audio")
OUT.mkdir(exist_ok=True)

mcp = FastMCP("fish-voice", stateless_http=True, host="0.0.0.0")

def get_key(ctx: Context) -> str:
    return ctx.request_context.request.headers.get("x-fish-key", "")

async def tts(client, key, text, rid):
    r = await client.post(
        "https://api.fish.audio/v1/tts",
        json={"text": text, "reference_id": rid, "format": "mp3"},
        headers={"Authorization": f"Bearer {key}", "model": MODEL},
    )
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text[:200]}"
    name = f"{uuid.uuid4().hex}.mp3"
    (OUT / name).write_bytes(r.content)
    return f"{BASE}/audio/{name}"

@mcp.tool()
async def list_my_voices(ctx: Context) -> str:
    """User ki Fish Audio voices dikhao (naam -> reference_id)."""
    key = get_key(ctx)
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(
            "https://api.fish.audio/model",
            params={"self": "true", "page_size": 50},
            headers={"Authorization": f"Bearer {key}"},
        )
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text[:200]}"
    items = r.json().get("items", [])
    return "\n".join(f"{i.get('title')} -> {i.get('_id')}" for i in items) or "Koi voice nahi mili"

@mcp.tool()
async def search_public_voices(ctx: Context, title: str = "", language: str = "", tag: str = "", page: int = 1) -> str:
    """Fish Audio ki PUBLIC voice library me voices dhundo (naam -> reference_id). language jaise: hi, en. title aur tag optional filter hain."""
    key = get_key(ctx)
    params = {"page_size": 20, "page_number": page, "sort_by": "task_count"}
    if title:
        params["title"] = title
    if language:
        params["language"] = language
    if tag:
        params["tag"] = tag
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.get(
            "https://api.fish.audio/model",
            params=params,
            headers={"Authorization": f"Bearer {key}"},
        )
    if r.status_code != 200:
        return f"Error {r.status_code}: {r.text[:200]}"
    data = r.json()
    items = data.get("items", [])
    if not items:
        return "Koi voice nahi mili"
    lines = [f"{i.get('title')} [{','.join(i.get('languages') or [])}] -> {i.get('_id')}" for i in items]
    return f"Total: {data.get('total')}\n" + "\n".join(lines)

@mcp.tool()
async def make_voiceover(segments: list[dict], ctx: Context) -> str:
    """Script ka voiceover banao. segments = [{"character": "...", "reference_id": "...", "text": "..."}].
    Har dialogue alag segment. reference_id hamesha user ki di hui list se lo, kabhi guess mat karo."""
    key = get_key(ctx)
    for s in segments:
        if not s.get("reference_id"):
            return f"Error: '{s.get('character', '?')}' ki reference_id missing hai. User se pucho ya list_my_voices chalao."
    out = []
    async with httpx.AsyncClient(timeout=180) as c:
        for i, s in enumerate(segments, 1):
            link = await tts(c, key, s["text"], s["reference_id"])
            out.append(f"{i}. {s.get('character', '')}: {link}")
    return "\n".join(out)

@mcp.custom_route("/audio/{name}", methods=["GET"])
async def audio(request):
    p = OUT / Path(request.path_params["name"]).name
    if not p.exists():
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(p, media_type="audio/mpeg")

class KeyFromPath:
    def __init__(self, app):
        self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            parts = scope["path"].strip("/").split("/")
            if len(parts) == 2 and parts[1] == "mcp":
                scope = dict(scope)
                scope["headers"] = list(scope["headers"]) + [(b"x-fish-key", parts[0].encode())]
                scope["path"] = "/mcp"
                scope["raw_path"] = b"/mcp"
        await self.app(scope, receive, send)

if __name__ == "__main__":
    uvicorn.run(KeyFromPath(mcp.streamable_http_app()), host="0.0.0.0", port=PORT, access_log=False)
