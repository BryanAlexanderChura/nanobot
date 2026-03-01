# Herramientas Disponibles

## Consulta CRM

### consulta
Consulta información de clientes, pedidos, precios y tracking en el sistema.
```
consulta(query: str) -> str
```

Úsala para responder preguntas sobre:
- Estado de pedidos y entregas
- Precios de servicios
- Historial del cliente
- Disponibilidad y horarios

## Cuidado Textil

### consulta_cuidado
Consulta guías de cuidado de prendas y tratamiento de manchas.
```
consulta_cuidado(query: str) -> str
```

Tiene referencias detalladas sobre:
- Tipos de prendas (denim, delicados, fibras sintéticas, etc.)
- Tipos de manchas (grasas, taninos, enzimáticas, etc.)
- Instrucciones de lavado y tratamiento

## Comunicación

### message
Envía un mensaje al cliente en su canal de chat.
```
message(content: str) -> str
```

### handoff
Transfiere la conversación a otro agente especializado.
```
handoff(target: str, message: str) -> str
```

Usa handoff cuando:
- El cliente necesita algo fuera de tu alcance
- Se requiere una acción operativa (modificar pedido, programar recojo)
- Necesitas escalar a un operador humano
