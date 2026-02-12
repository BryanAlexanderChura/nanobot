"""Tool de cuidado textil: consulta guías de prendas y manchas."""

from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool

# Mapeo: palabras clave → archivo de referencia
PRENDAS = {
    "fibras-vegetales": ["algodon", "lino", "cañamo", "canamo", "yute", "bambu", "bambú"],
    "fibras-animales": ["lana", "cachemira", "cashmere", "seda", "mohair", "alpaca", "angora"],
    "fibras-regeneradas": ["rayon", "rayón", "viscosa", "acetato"],
    "fibras-sinteticas": ["poliester", "poliéster", "nylon", "spandex", "elastano", "lycra", "acrilico", "acrílico", "microfibra"],
    "gorras": ["gorra", "cachucha", "cap"],
    "panales-tela": ["pañal", "panal", "pañales", "panales"],
    "delicados": ["encaje", "lenceria", "lencería", "sosten", "sostén", "brasier", "medias", "tul"],
    "elastico": ["elastico", "elástico", "elastica", "elástica", "fruncido"],
    "estampados": ["estampado", "estampada", "serigrafia", "serigrafía", "grafico", "gráfico", "camiseta estampada"],
    "denim": ["jean", "jeans", "mezclilla", "denim"],
    "almohadas": ["almohada", "almohadas", "cojin", "cojín"],
    "zapatillas": ["zapatilla", "zapatillas", "sneaker", "sneakers", "tenis", "lona"],
    "trajes-bano": ["traje de baño", "bañador", "bikini", "lycra"],
}

MANCHAS = {
    "grasas": ["aceite", "grasa", "maquillaje", "mantequilla", "manteca", "mayonesa"],
    "enzimaticas": ["sangre", "huevo", "pasto", "hierba", "sudor", "orina", "vomito", "vómito", "heces"],
    "taninos": ["vino", "cafe", "café", "te", "té", "jugo", "baya", "bayas", "arandano", "arándano", "fresa", "curcuma", "cúrcuma", "mostaza", "soja"],
    "particulas": ["lodo", "barro", "tierra", "arcilla", "ceniza", "hollin", "hollín", "arena"],
    "especiales": ["tinta", "pegamento", "cera", "oxido", "óxido", "pintura", "esmalte", "moho", "hongo"],
}


def _match(query: str, mapping: dict[str, list[str]]) -> str | None:
    """Busca la primera coincidencia en el mapeo."""
    q = query.lower().strip()
    for filename, keywords in mapping.items():
        for kw in keywords:
            if kw in q:
                return filename
    return None


class CuidadoTextilTool(Tool):
    """Consulta guías de cuidado de prendas y tratamiento de manchas."""

    def __init__(self, references_dir: str):
        self._refs = Path(references_dir)

    name = "consulta_cuidado"
    description = (
        "Consulta guías de cuidado textil. Parámetros opcionales: "
        "prenda (tipo de tela/prenda) y mancha (tipo de mancha). "
        "Retorna instrucciones específicas de lavado, secado, planchado y tratamiento."
    )
    parameters = {
        "type": "object",
        "properties": {
            "prenda": {
                "type": "string",
                "description": "Tipo de prenda o tela (ej: seda, algodon, jeans, gorra)"
            },
            "mancha": {
                "type": "string",
                "description": "Tipo de mancha (ej: cafe, sangre, aceite, tinta)"
            },
        },
    }

    async def execute(self, prenda: str = "", mancha: str = "", **kwargs: Any) -> str:
        results = []

        if prenda:
            filename = _match(prenda, PRENDAS)
            if filename:
                results.append(self._read(f"prendas/{filename}.md"))
            else:
                opciones = ", ".join(sorted(PRENDAS.keys()))
                results.append(f"Prenda no identificada: '{prenda}'. Opciones: {opciones}")

        if mancha:
            filename = _match(mancha, MANCHAS)
            if filename:
                results.append(self._read(f"manchas/{filename}.md"))
            else:
                opciones = ", ".join(sorted(MANCHAS.keys()))
                results.append(f"Mancha no identificada: '{mancha}'. Opciones: {opciones}")

        if not results:
            return "Indica al menos un tipo de prenda o mancha para consultar."

        return "\n\n---\n\n".join(results)

    def _read(self, relative_path: str) -> str:
        fp = self._refs / relative_path
        if not fp.exists():
            return f"Error: archivo no encontrado: {relative_path}"
        return fp.read_text(encoding="utf-8")
