---
name: cuidado-textil
description: Referencias internas para la herramienta consulta_cuidado (manchas y tipos de prendas).
---

# Cuidado Textil

Esta skill se mantiene solo como documentacion de apoyo.

En modo lavanderia, el agente debe usar `consulta_cuidado` y no `read_file`.

## Uso operativo esperado

1. Si el usuario consulta por manchas o telas, usar `consulta_cuidado`.
2. Si el usuario confirma `mancha` y `prenda`, enviar ambos parametros en una sola llamada.
3. Si falta un dato, responder con pasos seguros y pedir confirmacion concreta.

## Referencias disponibles

- `references/manchas/*.md`
- `references/prendas/*.md`

## Nota

Las respuestas de cuidado deben cerrar con:
`Fuente: The Laundry Book — Jerry y Zach Pozniak.`
