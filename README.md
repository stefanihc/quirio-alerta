# Quirio Alerta Huaicos

Aplicativo de alerta temprana de huaicos para la quebrada Quirio (Nicolás de Piérola, Lurigancho-Chosica, Lima).
Proyecto Quirio · UNI-FIGMM, Ingeniería Geológica.

- `index.html`: la app (se instala en el celular desde el navegador: "Agregar a pantalla de inicio").
- `estado.json`: color y avisos actuales. Lo escribe el motor cada hora.
- `motor/motor_quirio.py`: lee la lluvia satelital GPM IMERG Early (NASA) de las últimas 72 h y el pronóstico ECMWF (Open-Meteo).
- `.github/workflows/motor.yml`: corre el motor cada hora en GitHub.

Umbrales preliminares (GPM Early, lluvia de 3 días): amarillo ≥ 5 mm, naranja ≥ 9.6 mm.
El rojo y la evacuación los declara el COE de Lurigancho-Chosica, no la app.
