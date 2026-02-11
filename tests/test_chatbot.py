"""Interactive chatbot test simulating a WhatsApp customer."""

import asyncio
import os
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


async def main():
    from nanobot.config.loader import load_config
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.factory import create_provider
    from nanobot.agent.loop import AgentLoop

    config = load_config()
    bus = MessageBus()
    provider = create_provider(config)

    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        safe_mode=True,
    )

    sb_url = os.environ.get("SUPABASE_URL", "")
    sb_key = os.environ.get("SUPABASE_SERVICE_KEY", "")

    print("Chatbot Lavanderia GAR - Test Mode")
    print(f"  Telefono: {PHONE}")
    print(f"  Modelo:   {agent.model}")
    print(f"  Tools:    {agent.tools.tool_names}")
    print(f"  Supabase: {'OK' if sb_url and sb_key else 'SIN CREDENCIALES'}")
    print("-" * 50)
    print("Escribe como si fueras un cliente. Ctrl+C para salir.\n")

    session_key = f"whatsapp:{PHONE}"

    while True:
        try:
            user_input = input("Cliente: ")
            if not user_input.strip():
                continue

            t0 = time.time()
            response = await agent.process_direct(
                content=user_input,
                session_key=session_key,
                channel="whatsapp",
                chat_id=f"{PHONE}@s.whatsapp.net",
            )
            elapsed = time.time() - t0

            print(f"Bot ({elapsed:.1f}s): {response}\n")

        except (KeyboardInterrupt, EOFError):
            print("\nFin del test.")
            break
        except Exception as e:
            print(f"Error: {e}\n")
            break


if __name__ == "__main__":
    asyncio.run(main())
