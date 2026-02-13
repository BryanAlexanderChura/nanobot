"""Interactive chatbot test simulating a WhatsApp customer."""

import asyncio
import os
import re
import sys
import time
from pathlib import Path

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Ensure project root is in path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TEST_WORKSPACE = ROOT / "workspace"

# Load .env automatically
ENV_FILE = ROOT / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition("=")
        if key and val:
            os.environ.setdefault(key.strip(), val.strip())


PHONE = "51987654321"  # Simulated customer phone
RESET_TEST_MEMORY = os.environ.get("NANOBOT_TEST_RESET_MEMORY", "1") == "1"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def parse_image_input(raw: str) -> tuple[str | None, str | None]:
    """
    Parse test command:
      /img <ruta_imagen> | <prompt opcional>
    """
    if not raw.lower().startswith("/img "):
        return None, None

    payload = raw[5:].strip()
    if not payload:
        return "", ""

    if "|" in payload:
        path_part, prompt_part = payload.split("|", 1)
        image_path = path_part.strip()
        prompt = prompt_part.strip() or "Describe esta imagen."
    else:
        image_path = payload
        prompt = "Describe esta imagen."

    return image_path, prompt


def parse_inline_image_path(raw: str) -> tuple[str, list[str] | None]:
    """
    Parse inline image path inside free text.
    Example:
      "tengo esta mancha .cp-images/foto.png"
    """
    tokens = raw.split()
    for token in reversed(tokens):
        # Remove trailing punctuation only; keep leading dots for paths like ".cp-images/..."
        cleaned = token.rstrip(".,;:!?'\"()[]{}")
        if not cleaned:
            continue

        candidate = Path(cleaned).expanduser()
        if not candidate.is_file():
            candidate = (ROOT / cleaned).resolve()
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in IMAGE_EXTENSIONS:
            continue

        cleaned_text = re.sub(re.escape(token), "", raw, count=1).strip()
        if not cleaned_text:
            cleaned_text = "Describe esta imagen."
        return cleaned_text, [str(candidate)]

    return raw, None


def reset_test_state(workspace: Path) -> None:
    """Reset persisted session + memory for deterministic latency tests."""
    removed = 0

    # Clean stale session to avoid leaking old conversation context
    session_file = Path.home() / ".nanobot" / "sessions" / f"whatsapp_{PHONE}@s.whatsapp.net.jsonl"
    if session_file.exists():
        session_file.unlink()
        removed += 1
        print("[Sesion anterior limpiada]")

    # Clean agent memory files (new structure: agents/{entity}/memory/)
    for agent_dir in (workspace / "agents").iterdir() if (workspace / "agents").exists() else []:
        mem_dir = agent_dir / "memory"
        if mem_dir.exists():
            for fp in mem_dir.glob("*.md"):
                fp.unlink()
                removed += 1

    print(f"[Test limpio] persistencia reiniciada ({removed} archivos)")


async def main():
    from nanobot.config.loader import load_config
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.factory import create_provider
    from nanobot.agent.loop import AgentLoop, _split_chunks

    config = load_config()
    bus = MessageBus()
    provider = create_provider(config)
    workspace_path = TEST_WORKSPACE if TEST_WORKSPACE.exists() else config.workspace_path

    if RESET_TEST_MEMORY:
        reset_test_state(workspace_path)
    else:
        print("[Test limpio desactivado] NANOBOT_TEST_RESET_MEMORY=0")

    defaults = config.agents.defaults
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=workspace_path,
        entity="lavanderia",
        allowed_tools=["safe", "comms"],
        temperature=defaults.temperature,
        max_tokens=defaults.max_tokens,
        thinking=defaults.thinking,
    )

    sb_url = os.environ.get("SUPABASE_URL", "")
    sb_key = os.environ.get("SUPABASE_SERVICE_KEY", "")

    print("Chatbot Lavanderia GAR - Test Mode")
    print(f"  Telefono: {PHONE}")
    print(f"  Modelo:   {agent.model}")
    print(f"  Entity:   {agent.entity}")
    print(f"  Workspace:{workspace_path}")
    print(f"  Tools:    {agent.tools.tool_names}")
    print(f"  Temp:     {agent.temperature}")
    print(f"  MaxTok:   {agent.max_tokens}")
    print(f"  Thinking: {agent.thinking}")
    print(f"  Reset:    {'ON' if RESET_TEST_MEMORY else 'OFF'}")
    print(f"  Supabase: {'OK' if sb_url and sb_key else 'SIN CREDENCIALES'}")
    print("-" * 50)
    print("Escribe como si fueras un cliente. Ctrl+C para salir.\n")
    print("Tip vision: /img <ruta_imagen> | <pregunta opcional>\n")
    print("Tambien puedes pegar una ruta de imagen al final del mensaje.\n")

    session_key = f"whatsapp:{PHONE}"

    while True:
        try:
            user_input = input("Cliente: ")
            if not user_input.strip():
                continue

            content = user_input
            media_paths: list[str] | None = None
            img_path, img_prompt = parse_image_input(user_input)
            if img_path is not None:
                image_file = Path(img_path).expanduser()
                if not image_file.is_file():
                    print(f"Error: imagen no encontrada -> {image_file}\n")
                    continue
                content = img_prompt or "Describe esta imagen."
                media_paths = [str(image_file)]
                print(f"[Imagen cargada] {image_file}")
            else:
                content, media_paths = parse_inline_image_path(user_input)
                if media_paths:
                    print(f"[Imagen detectada] {media_paths[0]}")

            t0 = time.time()
            response = await agent.process_direct(
                content=content,
                session_key=session_key,
                channel="whatsapp",
                chat_id=f"{PHONE}@s.whatsapp.net",
                media=media_paths,
            )
            elapsed = time.time() - t0

            chunks = _split_chunks(response)
            if len(chunks) == 1:
                print(f"Bot ({elapsed:.1f}s): {chunks[0]}\n")
            else:
                print(f"Bot ({elapsed:.1f}s):")
                for i, chunk in enumerate(chunks, 1):
                    print(f"  [{i}] {chunk}")
                print()

        except (KeyboardInterrupt, EOFError):
            print("\nFin del test.")
            break
        except Exception as e:
            print(f"Error: {e}\n")
            break


if __name__ == "__main__":
    asyncio.run(main())
