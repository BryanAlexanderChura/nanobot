"""Supabase tool for laundry CRM operations."""

import json
import os
from typing import Any

from nanobot.agent.tools.base import Tool


_client_cache = None


async def _get_client():
    """Create and cache async Supabase client lazily."""
    global _client_cache
    if _client_cache is not None:
        return _client_cache

    from supabase import acreate_client

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")

    _client_cache = await acreate_client(url, key)
    return _client_cache


class SupabaseTool(Tool):
    """Query laundry CRM data from Supabase."""

    name = "consulta"
    description = (
        "Consulta datos del negocio de lavanderia. "
        "Acciones: catalogo, mi_pedido, tracking, servicios, horarios."
    )
    parameters = {
        "type": "object",
        "properties": {
            "accion": {
                "type": "string",
                "enum": ["catalogo", "mi_pedido", "tracking", "servicios", "horarios"],
                "description": (
                    "catalogo=ver precios por categoria, mi_pedido=estado del pedido del cliente, "
                    "tracking=seguimiento de entrega, servicios=categorias disponibles, "
                    "horarios=franjas de recojo/entrega"
                ),
            },
            "categoria": {
                "type": "string",
                "description": "Filtro por categoria: prendas, hogar, economico, especiales",
            },
            "busqueda": {
                "type": "string",
                "description": "Busqueda por nombre de servicio (ej: camisa, sabana)",
            },
            "sucursal_id": {
                "type": "string",
                "description": "UUID de sucursal. Si no se da, se resuelve del cliente.",
            },
        },
        "required": ["accion"],
    }

    def __init__(self):
        self._phone: str | None = None
        self._cliente_cache: dict | None = None
        self._cliente_loaded = False

    def set_phone(self, phone: str) -> None:
        """Set current customer phone from WhatsApp chat_id."""
        # WhatsApp JID: 51987654321@s.whatsapp.net -> 51987654321
        raw_phone = phone.split("@")[0] if "@" in phone else phone
        if raw_phone != self._phone:
            self._phone = raw_phone
            self._cliente_cache = None
            self._cliente_loaded = False

    async def build_customer_context(self, chat_id: str) -> str:
        """Build customer context for the system prompt from chat id."""
        self.set_phone(chat_id)
        db = await _get_client()
        cliente = await self._get_cliente(db)

        name = cliente.get("nombre") if cliente else None
        if not name:
            return ""

        return (
            f"\n## Cliente actual\nNombre: {name}\nTelefono: {self._phone}\n"
            f"Saluda al cliente por su nombre."
        )

    # ------------------------------------------------------------------
    async def execute(self, accion: str, _ctx: dict | None = None, **kwargs: Any) -> str:
        try:
            db = await _get_client()
        except RuntimeError as e:
            return f"Error: {e}"

        handlers = {
            "servicios": self._servicios,
            "catalogo": self._catalogo,
            "mi_pedido": self._mi_pedido,
            "tracking": self._tracking,
            "horarios": self._horarios,
        }
        handler = handlers.get(accion)
        if not handler:
            return f"Error: accion '{accion}' no reconocida"

        try:
            return await handler(db, **kwargs)
        except Exception as e:
            return f"Error consultando datos: {e}"

    # ------------------------------------------------------------------
    # Acciones
    # ------------------------------------------------------------------

    async def _servicios(self, db, **_) -> str:
        """Return available service categories."""
        res = await (
            db.table("servicios_catalogo")
            .select("categoria")
            .eq("activo", True)
            .execute()
        )
        cats = sorted(set(r["categoria"] for r in res.data))
        if not cats:
            return "No hay categorias disponibles en este momento."

        lines = ["Categorias de servicios disponibles:\n"]
        for c in cats:
            lines.append(f"  - {c.title()}")
        lines.append("\nPregunta por una categoria para ver precios.")
        return "\n".join(lines)

    async def _catalogo(self, db, categoria: str = "", busqueda: str = "",
                         sucursal_id: str = "", **_) -> str:
        """Return prices filtered by category/search within a branch."""
        # Resolve branch
        sid = sucursal_id or await self._resolve_sucursal(db)

        query = (
            db.table("servicios_catalogo")
            .select("nombre, categoria, precio, unidad, tiempo_estimado_horas")
            .eq("activo", True)
        )
        if sid:
            query = query.eq("sucursal_id", sid)
        if categoria:
            query = query.eq("categoria", categoria.lower())
        if busqueda:
            query = query.ilike("nombre", f"%{busqueda}%")

        query = query.order("categoria").order("precio")
        res = await query.limit(10).execute()

        if not res.data:
            return "No se encontraron servicios con ese filtro."

        lines = []
        current_cat = ""
        for s in res.data:
            if s["categoria"] != current_cat:
                current_cat = s["categoria"]
                lines.append(f"\n{current_cat.title()}:")
            tiempo = f" (~{s['tiempo_estimado_horas']}h)" if s.get("tiempo_estimado_horas") else ""
            lines.append(f"  • {s['nombre']}: S/{s['precio']:.2f} por {s['unidad']}{tiempo}")

        total = len(res.data)
        if total == 10:
            lines.append("\n(Mostrando primeros 10 resultados. Se mas especifico para ver mas.)")
        return "\n".join(lines)

    async def _mi_pedido(self, db, **_) -> str:
        """Return active orders for the current customer."""
        cliente = await self._get_cliente(db)
        if not cliente:
            return "No encontre tu cuenta. Es tu primera vez con nosotros?"

        res = await (
            db.table("pedidos")
            .select("codigo, estado, importe, cargo_delivery, created_at, observaciones")
            .eq("cliente_id", cliente["cliente_id"])
            .in_("estado", ["registrado", "en_proceso", "terminado", "mensaje_enviado"])
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )

        if not res.data:
            return "No tienes pedidos activos en este momento."

        estado_emoji = {
            "registrado": "Registrado",
            "en_proceso": "En proceso",
            "terminado": "Terminado",
            "mensaje_enviado": "Listo para entrega",
            "entregado": "Entregado",
        }

        lines = ["Tus pedidos activos:\n"]
        for p in res.data:
            estado = estado_emoji.get(p["estado"], p["estado"])
            total = (p["importe"] or 0) + (p["cargo_delivery"] or 0)
            lines.append(f"  Pedido {p['codigo']}")
            lines.append(f"  Estado: {estado}")
            lines.append(f"  Total: S/{total:.2f}")
            if p.get("observaciones"):
                lines.append(f"  Nota: {p['observaciones']}")
            lines.append("")
        return "\n".join(lines)

    async def _tracking(self, db, **_) -> str:
        """Return delivery tracking for active orders."""
        cliente = await self._get_cliente(db)
        if not cliente:
            return "No encontre tu cuenta."

        # Get active orders with deliveries
        pedidos = await (
            db.table("pedidos")
            .select("codigo")
            .eq("cliente_id", cliente["cliente_id"])
            .in_("estado", ["registrado", "en_proceso", "terminado", "mensaje_enviado"])
            .execute()
        )
        if not pedidos.data:
            return "No tienes pedidos activos."

        codigos = [p["codigo"] for p in pedidos.data]
        entregas = await (
            db.table("entregas")
            .select("pedido_codigo, tipo, estado, fecha_programada, franja_horaria, estimado_llegada")
            .in_("pedido_codigo", codigos)
            .in_("estado", ["pendiente", "asignado", "en_camino"])
            .order("fecha_programada")
            .execute()
        )

        if not entregas.data:
            return "No hay entregas programadas en este momento."

        estado_emoji = {
            "pendiente": "Pendiente",
            "asignado": "Asignado",
            "en_camino": "En camino",
        }

        lines = ["Seguimiento de entregas:\n"]
        for e in entregas.data:
            estado = estado_emoji.get(e["estado"], e["estado"])
            lines.append(f"  Pedido {e['pedido_codigo']} ({e['tipo']})")
            lines.append(f"  Estado: {estado}")
            if e.get("fecha_programada"):
                lines.append(f"  Fecha: {e['fecha_programada']}")
            if e.get("franja_horaria"):
                lines.append(f"  Horario: {e['franja_horaria']}")
            lines.append("")
        return "\n".join(lines)

    async def _horarios(self, db, sucursal_id: str = "", **_) -> str:
        """Return available pickup/delivery time slots."""
        sid = sucursal_id or await self._resolve_sucursal(db)
        if not sid:
            return "Necesito saber tu sucursal para mostrarte horarios disponibles."

        from datetime import date

        try:
            res = await db.rpc(
                "fn_slots_disponibles_v1",
                {"p_sucursal_id": sid, "p_fecha": date.today().isoformat()},
            ).execute()
        except Exception:
            # Fallback if RPC doesn't exist or fails
            return (
                "Horarios de recojo y entrega:\n"
                "  Lunes a Sabado: 8:00 - 20:00\n"
                "  Domingos: 9:00 - 14:00\n\n"
                "Contactanos para agendar tu recojo."
            )

        if not res.data:
            return "No hay horarios disponibles para hoy. Intenta con otro dia."

        data = res.data if isinstance(res.data, dict) else res.data
        slots = data.get("slots", data) if isinstance(data, dict) else data

        if not slots:
            return "No hay horarios disponibles para hoy."

        lines = ["Horarios disponibles para hoy:\n"]
        for s in slots[:8]:
            if isinstance(s, dict):
                lines.append(f"  - {s.get('franja', s)}")
            else:
                lines.append(f"  - {s}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_cliente(self, db) -> dict | None:
        """Find customer by phone (tries telefono_whatsapp then telefono)."""
        if not self._phone:
            return None
        if self._cliente_loaded:
            return self._cliente_cache

        # Normalize: ensure +country format for WhatsApp field
        phone_with_plus = self._phone if self._phone.startswith("+") else f"+{self._phone}"

        # Try telefono_whatsapp first (primary for WhatsApp users)
        res = await (
            db.table("clientes")
            .select("cliente_id, nombre, sucursal_id, telefono, telefono_whatsapp")
            .eq("telefono_whatsapp", phone_with_plus)
            .limit(1)
            .execute()
        )
        if res.data:
            self._cliente_cache = res.data[0]
            self._cliente_loaded = True
            return self._cliente_cache

        # Fallback: try telefono field with raw number
        res = await (
            db.table("clientes")
            .select("cliente_id, nombre, sucursal_id, telefono, telefono_whatsapp")
            .eq("telefono", self._phone)
            .limit(1)
            .execute()
        )
        self._cliente_cache = res.data[0] if res.data else None
        self._cliente_loaded = True
        return self._cliente_cache

    async def _resolve_sucursal(self, db) -> str:
        """Get branch ID from current customer, or first active branch."""
        cliente = await self._get_cliente(db)
        if cliente and cliente.get("sucursal_id"):
            return cliente["sucursal_id"]

        # Fallback: first active branch
        res = await (
            db.table("sucursales")
            .select("id")
            .eq("estado", "Activa")
            .limit(1)
            .execute()
        )
        return res.data[0]["id"] if res.data else ""
