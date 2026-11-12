"""Manda una notificación de PRUEBA a todos los celulares que activaron avisos (se corre a mano desde GitHub)."""
import motor_quirio as m

m.avisar_celulares([{"color": "verde", "titulo": "Prueba de Quirio Alerta",
                     "texto": "Si ves esto, los avisos llegan a tu celular aunque la app esté cerrada. No es una alerta real."}],
                   "prueba", 0)
