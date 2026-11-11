# Plataforma de viajes y domicilios de comida: requisitos

La plataforma es el backend de un servicio como Uber y Uber Eats juntos: los pasajeros (riders) piden
viajes a conductores (drivers) y piden comida a restaurantes, que entregan repartidores (couriers). Es un
paquete de Python `rides` con una clase pública `Platform` cuyos métodos se listan en INTERFACE.md. Cada
requisito tiene un identificador.

## Reglas generales (X)

- **X1.** Los identificadores son un prefijo y un contador de seis dígitos, que empieza en 000001 para cada
  prefijo: `ADM-` administradores, `RID-` pasajeros, `DRV-` conductores, `CUR-` repartidores, `RST-`
  restaurantes, `VEH-` vehículos, `ZON-` zonas, `QTE-` cotizaciones, `RDE-` viajes, `ITM-` ítems del menú,
  `GRP-` grupos de opciones, `OPT-` opciones, `ORD-` pedidos de comida, `PM-` tarjetas, `PAY-` pagos,
  `TCK-` tickets. Una llamada fallida nunca consume un identificador.
- **X2.** El dinero es `Decimal` con dos decimales, redondeado half up (la mitad hacia arriba) solo donde
  una regla lo indica. Los argumentos de dinero aceptan `int`, `str` o `Decimal`; un `float` lanza
  `ValueError`.
- **X3.** Errores: una entrada inválida o una regla incumplida lanza `ValueError`; un identificador
  desconocido lanza `KeyError`; un actor que no tiene permitido hacer algo lanza `PermissionError`. Una
  llamada que lanza una excepción no cambia nada.
- **X4.** La plataforma tiene un reloj virtual. `Platform(start, db_path=None)` lo inicia en `start`;
  `now()` lo devuelve; `advance(minutes=0, seconds=0)` lo adelanta y procesa cada temporizador que vence,
  en orden temporal (vencimientos de ofertas, despachos programados, vencimientos de la aceptación de
  pedidos, reintentos de asignación de repartidores). Nada lee el reloj del sistema.
- **X5.** Cada lista o dict que devuelve la plataforma es un objeto nuevo; modificarlo no cambia nada.
- **X6.** Límites: un límite de tiempo escrito como "dentro de N" (within N), "dura N" (lasts N) o "durante
  N" (for N) se cumple mientras el tiempo transcurrido sea estrictamente menor que N (exactamente en N ya
  pasó); "hasta T" (until T) y "antes de T" (before T) significan estrictamente antes de T. Los límites de
  distancia, monto y cantidad escritos como "como máximo" (at most), "al menos" (at least) o "de a a b"
  (from a to b) incluyen sus extremos; las distancias se comparan después de redondear a 3 decimales (C1).
- **X7.** Tipos: "un int" es un `int` que no es un `bool` (las cadenas y los floats lanzan `ValueError`).
  Los identificadores, códigos, placas y números de licencia no se normalizan salvo que una regla lo diga. Las horas del día son cadenas `HH:MM` con dos dígitos cada una, de 00:00 a 23:59. A los nombres, motivos, comentarios
  y textos se les quitan los espacios de los extremos (stripped) antes de validarlos; una regla que exige que
  no estén vacíos rechaza uno hecho solo de espacios. Los argumentos de dinero con más de 2 decimales
  lanzan `ValueError`.
- **X8.** Precedencia de errores: un identificador desconocido (`KeyError`) se reporta antes que un problema
  de permisos (`PermissionError`), que se reporta antes que cualquier otro problema (`ValueError`). Actuar
  sobre una cuenta, viaje, pedido o medio de pago de otra persona lanza `PermissionError`. Usar una cuenta
  que no está `active` donde se necesita una activa lanza `ValueError`.

## A. Cuentas y seguridad

- **A1.** `register_rider`, `register_driver`, `register_courier`, `register_restaurant` y
  `create_admin` validan: al nombre se le quitan los espacios de los extremos, no está vacío y tiene como
  máximo 80 caracteres; al email se le quitan los espacios de los extremos, se pasa a minúsculas, coincide
  con `^[^@\s]+@[^@\s]+\.[^@\s]+$` y es único entre todas las cuentas; al teléfono no se le quitan los espacios, coincide con `^\+[1-9]\d{7,14}$` y es único entre todas las cuentas; la contraseña
  tiene al menos 10 caracteres, al menos una letra ASCII y al menos un dígito ASCII.
- **A2.** Las contraseñas se guardan solo como hashes PBKDF2-HMAC-SHA256 con una sal aleatoria de 16 bytes
  y al menos 100 000 iteraciones. La contraseña en texto plano nunca se guarda, se registra en logs ni se
  devuelve.
- **A3.** `authenticate(email, password)` devuelve el id de la cuenta (al email se le quitan los espacios de
  los extremos y se compara sin distinguir mayúsculas de minúsculas).
  Una contraseña incorrecta o un email desconocido lanza `PermissionError`. Después de 5 fallos consecutivos
  de una cuenta, esta se bloquea durante 15 minutos: durante el bloqueo incluso la contraseña correcta lanza
  `PermissionError`; un acierto reinicia el conteo. Los intentos durante un bloqueo no cuentan ni lo extienden; cuando el bloqueo termina el conteo vuelve a empezar en 0. Una cuenta
  suspendida no puede autenticarse.
- **A4.** `get_account(id)` devuelve `id`, `role` (`admin`, `rider`, `driver`, `courier`, `restaurant`),
  `name`, `email`, `phone` enmascarado como sus primeros 3 caracteres, luego un `*` por cada dígito oculto y
  luego sus últimos 4 caracteres (`+573001234567` → `+57******4567`), y `status`.
- **A5.** Estado: los pasajeros y los administradores empiezan `active`; los conductores, repartidores y
  restaurantes empiezan `pending` y pasan a `active` con `approve(admin_id, account_id)`.
  `suspend(admin_id, account_id, reason)` pone `suspended` (motivo no vacío) y fuerza a un trabajador a
  desconectarse (offline); `reactivate` pone `active`. Solo los administradores pueden aprobar, suspender o
  reactivar (`PermissionError` en caso contrario); un administrador que se suspende a sí mismo lanza `ValueError`.
  Un motivo hecho solo de espacios cuenta como vacío.
- **A6.** El número de licencia de un conductor coincide con `^[A-Z0-9]{6,12}$` y `license_expires` debe ser posterior a la fecha actual. Una licencia es válida mientras la fecha actual sea
  anterior a `license_expires` (vence en esa fecha). El `birth_date` de un pasajero es opcional; cuando se da, el pasajero debe tener al menos 16 años.
- **A7.** El vehículo de un repartidor es uno de `bike`, `moto`, `car`.
- **A8.** `referral_code(rider_id)` es `REF` seguido de los seis dígitos del id del pasajero
  (`RID-000007` → `REF000007`). `register_rider(..., referral_code=...)` acepta un código existente de
  otro pasajero; un código desconocido lanza `ValueError`.

## B. Conductores, repartidores y vehículos

- **B1.** `add_vehicle(driver_id, plate, make, model, year, seats, category)`: la placa coincide con
  `^[A-Z]{3}\d{3}$` y es única; la marca y el modelo no están vacíos; el año es un int de 2012 al año
  actual + 1; la categoría es `economy`, `comfort`, `xl` o `moto`. Asientos: `moto` exactamente 1; `economy`
  y `comfort` de 4 a 6; `xl` de 6 a 8. `comfort` exige año 2018 o posterior.
- **B2.** `approve` de un conductor exige al menos un vehículo y una licencia no vencida; si no,
  `ValueError`.
- **B3.** `go_online(account_id, location, vehicle_id=None)`: los conductores deben estar `active`, dar uno
  de sus propios vehículos y tener una licencia no vencida en el momento actual; los repartidores deben estar `active` y no dar ningún vehículo (`ValueError` si dan uno). Un id de vehículo desconocido
  lanza `KeyError`; un vehículo de otro conductor lanza `PermissionError`. El trabajador pasa a `available` en esa ubicación. Los trabajadores empiezan `offline`.
- **B4.** `go_offline` pone `offline`; un trabajador con un viaje o pedido activo lanza `ValueError`.
  `update_location` cambia la ubicación de un trabajador conectado (`ValueError` cuando está offline).
- **B5.** `worker_status(account_id)` devuelve `status` (`offline`, `available`, `busy`), `location`
  (`None` cuando está offline) y `vehicle_id`.

## C. Geografía

- **C1.** `distance_km(a, b)` es la distancia haversine con radio de la Tierra 6371.0 km, como `Decimal`
  redondeado half up a 3 decimales.
- **C2.** `add_zone(admin_id, name, center, radius_km, speed_kmh, airport=False)`: radio de 0.5 a 50,
  velocidad de 5 a 120 (solo administradores). Una ubicación pertenece a la primera zona, en orden de
  creación, cuyo centro está como máximo a `radius_km` de distancia. Un viaje o pedido cuya recogida,
  destino o restaurante está fuera de todas las zonas lanza `ValueError`.
- **C3.** `eta_minutes(a, b)` es `ceil(distance_km(a, b) / speed_kmh x 60)` usando la zona de `a`, y al
  menos 1. Si `a` está fuera de todas las zonas la velocidad es 30.

## D. Cotizaciones y tarifas de viajes

- **D1.** `quote_ride(rider_id, pickup, dropoff, category)` devuelve `quote_id`, `category`,
  `distance_km = distance_km(pickup, dropoff)`, `duration_min = eta_minutes(pickup, dropoff)`, `surge`,
  `fare` y `expires_at = now + 5 minutes`. La distancia debe ser de 0.2 a 150 km. El pasajero debe estar activo (`ValueError` en caso contrario).
- **D2.** Tarifas por categoría (base, por km, por minuto, mínima, tarifa de reserva): economy 2.50, 1.10,
  0.25, 6.00, 1.50; comfort 3.50, 1.50, 0.35, 9.00, 1.50; xl 4.00, 1.80, 0.40, 11.00, 2.00; moto 1.50,
  0.70, 0.15, 4.00, 1.00.
- **D3.** Tarifa: `metered = base + per_km x distance + per_min x minutes`; multiplicar por 1.2 cuando la
  hora de la cotización es de 22:00 a 05:59 (nocturna); multiplicar por el surge; tomar el máximo con la
  tarifa mínima; sumar la tarifa de reserva; sumar 5.00 si la recogida o el destino está en una zona de
  aeropuerto; redondear half up a 2 decimales.
- **D4.** Pedir un viaje con una cotización que venció, pertenece a otro pasajero o ya se usó lanza
  `ValueError`.

## E. Precio dinámico (surge pricing)

- **E1.** El surge de una cotización depende de la zona de la recogida: `demand` es el número de viajes en
  estado `requested` cuya recogida está en esa zona, más uno; `supply` es el número de conductores `available` sin una oferta pendiente
  en esa zona cuyo vehículo tiene la categoría cotizada; `ratio = demand / max(supply, 1)`.
- **E2.** Multiplicador según el ratio, con cada límite incluido (up to): hasta 1.0 → 1.0; hasta 1.5 → 1.2;
  hasta 2.0 → 1.5; hasta 3.0 → 1.8; por encima de 3.0 → 2.0. El resultado queda limitado por el tope de la
  zona (por defecto 2.0); `set_surge_cap` (solo administradores) recibe un tope de 1.0 a 3.0.
- **E3.** El surge se fija al cotizar y se usa para la tarifa final.

## F. Despacho (dispatch)

- **F1.** `request_ride` crea un viaje en estado `requested`, coloca una retención (hold) por la tarifa
  cotizada en el medio de pago (ver O) y ofrece el viaje al mejor conductor. Un pasajero con otro viaje en
  `requested`, `driver_assigned`, `arrived` o `in_progress` lanza `ValueError` (los viajes programados no
  cuentan). Un pasajero puede cotizar en cualquier momento.
- **F2.** Conductores elegibles: `available`, `active`, vehículo de la categoría del viaje, como máximo a 8
  km de la recogida, calificación de al menos 4.60 o sin calificaciones aún, a los que no se les ofreció
  este viaje antes, y sin una oferta pendiente de otro viaje. El mejor es el más cercano; los empates se resuelven a favor de la calificación más alta (sin calificación cuenta como 5.00), luego del id de conductor menor.
- **F3.** La oferta dura 15 segundos (`offered_to` contiene el id del conductor). `accept_ride` por ese
  conductor dentro del tiempo pasa el viaje a `driver_assigned` y al conductor a `busy`. Un conductor al que nunca se le ofreció el viaje lanza `PermissionError`; un conductor cuya oferta venció o
  fue rechazada lanza `ValueError`.
- **F4.** `decline_ride`, o una oferta que vence, ofrece el viaje al siguiente mejor conductor. Después de
  que 5 conductores rechazaron o dejaron vencer la oferta, o cuando ningún conductor es elegible, el viaje
  pasa a `no_driver`, la retención se libera y se notifica al pasajero.
- **F5.** `idempotency_key`: un segundo `request_ride` con la misma clave y el mismo pasajero devuelve el
  mismo id de viaje y no cambia nada.

## G. Ciclo de vida del viaje y cancelaciones

- **G1.** `driver_arrived` (conductor asignado, estado `driver_assigned`, conductor como máximo a 0.2 km de
  la recogida (within 0.2 km)) → `arrived`. `start_ride` (desde `arrived`) → `in_progress`. `complete_ride`
  (desde `in_progress`) → `completed`; el conductor pasa a `available`. Cualquier otro orden lanza
  `ValueError`; otro conductor lanza `PermissionError`.
- **G2.** Tarifa de espera: cuando el viaje empieza más de 5 minutos después de la llegada, el pasajero paga
  0.30 por cada minuto completo después de los 5.
- **G3.** `cancel_ride(actor_id, ride_id, reason)` lo puede llamar el pasajero, el conductor asignado o un
  administrador. Un pasajero que cancela en `requested` no paga nada; en `driver_assigned` o `arrived` no
  paga nada dentro de los 2 minutos siguientes a la asignación y, si no, paga una tarifa de 3.00 (economy,
  moto) o 5.00 (comfort, xl). La tarifa va completa al conductor. Un conductor que cancela devuelve el viaje
  a `requested` y este se ofrece de nuevo, nunca a ese conductor. Una cancelación de un administrador es gratis para todos. Un conductor cuyo viaje se cancela pasa a `available`. Cada
  cancelación notifica al pasajero y al conductor asignado, si lo hay, con `ride_cancelled`. Los viajes
  `in_progress`, `completed`, `cancelled` o `no_driver` no se pueden cancelar. Un viaje en `scheduled` sigue
  I3; una vez despachado sigue estas reglas.
- **G4.** Un pasajero con 3 viajes cancelados con tarifa en las últimas 24 horas no puede pedir viajes durante
  24 horas después del tercero (`ValueError`). Las cotizaciones siguen permitidas. Un conductor con 3 cancelaciones en las últimas 24 horas queda forzado a offline y
  no puede conectarse durante 12 horas (`ValueError`).

## H. Tarifa final y pago de un viaje

- **H1.** `complete_ride(driver_id, ride_id, route)`: route es una lista de al menos 2 ubicaciones; la
  distancia real es la suma de las distancias haversine entre puntos consecutivos (sin redondear por
  segmento), redondeada half up a 3 decimales; los minutos reales son los minutos entre el inicio y la
  finalización redondeados hacia arriba, al menos 1.
- **H2.** Precio por adelantado: el pasajero paga la tarifa cotizada, salvo que la distancia real sea más
  de un 20 % mayor que la distancia cotizada; en ese caso la tarifa se recalcula como en D3 con la distancia
  y los minutos reales, el factor nocturno y el surge de la cotización y el recargo de aeropuerto de la
  cotización.
- **H3.** `charged = fare + wait fee - discount`, nunca menor que 0. El pago se captura por `charged` y el
  resto de la retención se libera. `ride()` muestra `quoted_fare`, `fare`, `distance_km` y
  `duration_min` (los reales después de la finalización), `wait_fee`, `discount`, `tip` y `charged`. Antes de
  la finalización `fare` es igual a `quoted_fare` y `charged` es 0.00; una cancelación con tarifa pone
  `cancellation_fee` y `charged` en el valor de la tarifa.
- **H4.** `add_tip(rider_id, ride_id, amount)`: solo viajes completados, dentro de las 24 horas siguientes a
  la finalización, una vez, monto de 1.00 a 100.00; se cobra al medio de pago del viaje; va completa al
  conductor.

## I. Viajes programados

- **I1.** `schedule_ride` recibe un `pickup_at` de 30 minutos a 30 días después de ahora. El viaje queda
  `scheduled`, cotizado sin surge y con el factor nocturno de `pickup_at`. Todavía no se coloca ninguna
  retención.
- **I2.** 10 minutos antes de `pickup_at` el viaje se despacha: se coloca la retención y el viaje se ofrece
  como en F (estado `requested`). Si la retención falla, el viaje pasa a `cancelled` y el pasajero recibe una notificación `ride_cancelled`.
- **I3.** El pasajero puede cancelar un viaje programado gratis hasta 60 minutos antes de `pickup_at`;
  después la tarifa es 5.00, se cobra al medio de pago y se la queda la plataforma.

## J. Restaurantes y menús

- **J1.** `set_hours(restaurant_id, weekday, opens, closes)`: weekday de 0 (lunes) a 6; horas `HH:MM` con
  la apertura antes del cierre el mismo día; un intervalo por día de la semana, que reemplaza el anterior; un día de la semana sin intervalo está cerrado. `is_open` es
  verdadero cuando el restaurante está activo y la hora actual está en `[opens, closes)` del día de la
  semana actual.
- **J2.** `add_menu_item`: nombre no vacío; precio mayor que 0; categoría `food` o `alcohol`; minutos de
  preparación un int de 1 a 120. Los ítems empiezan disponibles.
- **J3.** `add_option_group(item_id, name, required, min_choices, max_choices)`: `0 <= min_choices <=
  max_choices`, `max_choices >= 1`, y un grupo obligatorio tiene `min_choices >= 1`. `add_option(group_id,
  name, price_delta)`: diferencia de precio de 0 a 100.
- **J4.** `menu(restaurant_id)` lista los ítems en orden de id, cada uno con `id`, `name`, `price`,
  `category`, `available` y `groups` (en orden de id, cada uno con `id`, `name`, `required`, `min_choices`,
  `max_choices` y `options` en orden de id, cada una con `id`, `name`, `price_delta`).

## K. Precio de los pedidos de comida

- **K1.** `place_order(rider_id, restaurant_id, lines, dropoff, payment_method_id, ...)`: lines es una lista
  de dicts con `item_id`, `quantity` (int de 1 a 20) y `options` opcional (lista de ids de opciones). El
  restaurante debe estar abierto; cada ítem debe pertenecerle y estar disponible; las opciones deben
  pertenecer al ítem; cada grupo obligatorio tiene entre min y max elecciones; un grupo opcional como máximo
  max; ninguna opción repetida. El destino debe estar como máximo a 10 km del restaurante. Al menos una línea. Los ids de ítem u opción desconocidos lanzan `KeyError`; una opción de otro ítem lanza
  `ValueError`. `order()["lines"]` lista las líneas en el orden dado, cada una con `item_id`, `quantity`,
  `options` (ids), `unit_price`, `amount` y `tax`.
- **K2.** Los ítems de alcohol exigen un pasajero con fecha de nacimiento y de al menos 18 años.
- **K3.** Precio unitario = precio del ítem + diferencias de las opciones; monto de la línea = unitario x
  cantidad; `subtotal` = suma.
- **K4.** `service_fee` = 5 % del subtotal redondeado half up, al menos 1.00 y como máximo 5.00.
  `small_order_fee` = 2.00 cuando el subtotal es menor que 10.00. `delivery_fee` = 1.99 + 0.50 x (km
  después de 2, del restaurante al destino), redondeado half up; 0.00 cuando el subtotal es al menos 35.00.
- **K5.** `tax` = suma sobre las líneas del monto de la línea x tasa (comida 8 %, alcohol 19 %), cada línea
  redondeada half up. `tip` de 0 a 50 % del subtotal. `total = subtotal + service_fee + delivery_fee +
  small_order_fee + tax - discount + tip`.
- **K6.** Se coloca una retención por el total en el medio de pago. `idempotency_key` se comporta como en F5.

## L. Ciclo de vida de los pedidos de comida

- **L1.** Estados: `placed` → `accepted` → `ready` → `picked_up` → `delivered`; además `rejected`,
  `expired` y `cancelled`.
- **L2.** El restaurante debe aceptar dentro de 5 minutos; si no, el pedido pasa a `expired` y la retención
  se libera. `accept_order` pone `ready_at = now + max prep minutes of its items` (el máximo de los minutos
  de preparación de sus ítems). `reject_order` → `rejected`, retención liberada. `mark_ready` desde
  `accepted`, en cualquier momento (no espera a `ready_at`).
- **L3.** `pick_up` por el repartidor asignado cuando está `ready` y como máximo a 0.2 km del restaurante (within 0.2 km); `deliver` por ese repartidor cuando está `picked_up` y
  como máximo a 0.2 km del destino (within 0.2 km): el pago se captura por el total (`charged`, que es 0.00 hasta entonces), el repartidor pasa a
  available salvo que lleve otro pedido.
- **L4.** `cancel_order` por el pasajero: en `placed` gratis (retención liberada); en `accepted` o `ready` el pasajero paga subtotal + impuesto (capturado,
  tipo de pago `order`) y las tarifas, la propina y el resto se liberan; después de `picked_up`
  lanza `ValueError`. Un administrador puede cancelar cualquier pedido no entregado, sin cobrar nada.

## M. Repartidores y agrupación de pedidos

- **M1.** Cuando un pedido se acepta se asigna al repartidor `available` y activo más cercano que esté como
  máximo a 6 km del restaurante; los empates se resuelven a favor del id menor. Un repartidor `bike` solo
  toma pedidos cuyo destino está como máximo a 4 km del restaurante. Sin repartidor el pedido sigue
  esperando y la asignación se reintenta en cada `advance` y cada vez que un repartidor se conecta o pasa a
  estar disponible.
- **M2.** Agrupación (batching): un repartidor que lleva exactamente un pedido no entregado (asignado, no
  recogido) recibe un segundo pedido del mismo restaurante antes que cualquier repartidor disponible cuando
  ambos pedidos se aceptaron dentro de 5 minutos uno del otro y sus destinos están como máximo a 2 km uno del
  otro. Nunca más de 2 pedidos.
- **M3.** Un repartidor que lleva pedidos está `busy`; `courier_id` se muestra en `order()`.
- **M4.** Pago al repartidor por pedido entregado: 2.50 + 0.60 x distancia del restaurante al destino,
  redondeado half up; el segundo pedido de una agrupación gana el 70 % de ese monto (redondeado half up); las
  propinas van completas al repartidor.

## N. Promociones y referidos

- **N1.** `create_promo` (solo administradores): al código se le quitan los espacios de los extremos, se pasa
  a mayúsculas, coincide con `^[A-Z0-9]{4,15}$` y es único; servicio `rides`, `eats` o `both`; tipo
  `percent` (valor un int de 1 a 50) o `fixed` (valor de 1.00 a 100.00); descuento máximo, gasto mínimo,
  inicio y fin, límites totales y por usuario, y solo primer pedido, opcionales.
- **N2.** Una promoción se usa mediante `request_ride(..., promo_code)` o `place_order(..., promo_code)`, una
  por viaje o pedido; los códigos coinciden sin distinguir mayúsculas de minúsculas. Debe existir, coincidir con el servicio, estar dentro de su ventana (desde `starts`, incluido, hasta `ends`, excluido), el monto (tarifa cotizada o subtotal) debe alcanzar el gasto mínimo, los límites no
  deben estar alcanzados y, cuando es solo para el primer pedido, el pasajero no tiene ningún viaje
  completado (rides) ni pedido entregado (eats). En caso contrario `ValueError`.
- **N3.** Descuento: porcentaje de la tarifa cotizada o del subtotal redondeado half up, limitado por el
  descuento máximo; fixed = min(valor, monto). Los usos cuentan cuando el viaje se completa o el pedido se
  entrega.
- **N4.** Referido: cuando un pasajero registrado con un código de referido completa su primer viaje, ambos
  pasajeros reciben 5.00 en sus billeteras, una vez.

## O. Pagos y billetera

- **O1.** `add_card(rider_id, number, exp_month, exp_year, cvc)`: el número es una cadena de 13 a 19 dígitos (sin espacios) y
  pasa la verificación de Luhn; el mes de vencimiento no debe ser anterior al mes actual; el cvc tiene 3
  dígitos (4 para amex). Marca: empieza con 4 → visa; de 51 a 55 → mastercard; 34 o 37 → amex; en otro caso
  `ValueError`. Solo se guardan la marca, los últimos 4 dígitos y el vencimiento, nunca el número ni el cvc.
- **O2.** Cada pasajero tiene una billetera con id `WAL-` más los seis dígitos del id del pasajero, listada primero por `payment_methods` (kind `wallet`, brand y last4 `None`); las tarjetas tienen kind `card`.
  `wallet_balance` es el saldo menos las retenciones activas. `top_up_wallet(rider_id, card_id, amount)` de 5.00 a 500.00 cobra a la tarjeta.
- **O3.** Retenciones (holds): una retención en tarjeta siempre funciona salvo para el número de tarjeta
  `4000000000000002`, cuyas retenciones y cobros fallan (`ValueError`). Una retención en billetera exige que
  el saldo menos las retenciones activas cubra el monto. Un medio de pago de otro pasajero lanza
  `PermissionError`.
- **O4.** `payments(account_id)` lista los cobros capturados, los reembolsos (negativos), las recargas y las
  propinas de un pasajero en orden temporal, cada uno con `id`, `kind` (`ride`, `order`, `cancellation_fee`, `tip`, `top_up`, `refund`), `ref` (el
  id del viaje o del pedido; el id de la tarjeta para una recarga), `method_id`, `amount` y `at`. Solo estas
  entradas consumen identificadores `PAY-`; las retenciones no.

## P. Ganancias y liquidaciones

- **P1.** Ganancias del conductor por un viaje completado: 75 % de (tarifa - tarifa de reserva - recargo de
  aeropuerto) redondeado half up, más el recargo de aeropuerto, la tarifa de espera y la propina; las
  tarifas de cancelación de G3 van completas al conductor. Ganancias del repartidor como en M4.
- **P2.** `earnings(worker_id, start, end)`: cuenta los viajes o pedidos completados en `[start, end)` y
  devuelve `trips` (viajes completados o pedidos entregados), `fares` (la parte de la tarifa del conductor,
  incluido el recargo de aeropuerto, o el pago al repartidor de M4), `tips`, `fees` (tarifas de espera y
  tarifas de cancelación), `bonus` y `total` (la suma de los cuatro montos).
- **P3.** Bono semanal: un conductor que completa al menos 20 viajes en una semana (de lunes 00:00 al lunes
  siguiente 00:00) gana 30.00 por esa semana.
- **P4.** `run_payouts(admin_id, week_start)` (solo administradores; `week_start` es un `date` que es lunes y cuya semana
  ya terminó, `now >= week_start + 7 days`, si no `ValueError`) le paga a cada trabajador las
  ganancias de esa semana, bono incluido, una vez por semana (una segunda ejecución para la misma semana lanza
  `ValueError`), y devuelve `{worker_id: amount}` para los montos mayores que 0.

## Q. Calificaciones

- **Q1.** `rate(actor_id, ref_id, stars, comment, target)`: stars un int de 1 a 5; comment de como máximo
  500 caracteres. En un viaje completado el pasajero califica al conductor (`target="driver"`) y el conductor califica al pasajero
  (`target="rider"`), dentro de 7
  días desde la finalización, una vez cada uno. En un pedido entregado el pasajero califica al `restaurant` y
  al `courier` (target), una vez cada uno, dentro de 7 días.
- **Q2.** `rating(account_id)` es la media de las últimas 100 calificaciones recibidas, redondeada half up a 2
  decimales, o `None`.
- **Q3.** Un conductor cuya calificación baja de 4.60 con al menos 20 calificaciones pasa a `under_review`: se le fuerza a offline cuando no tiene un viaje activo (si no, cuando el viaje termina) y no puede conectarse
  (`ValueError`) hasta que un administrador lo reactive. El umbral compara la calificación redondeada de Q2.

## R. Soporte, seguridad y compartir viaje

- **R1.** `report_issue(actor_id, ref_id, kind, text)`: el actor es el pasajero, o el conductor de un viaje (`PermissionError` en caso contrario); cualquier estado;
  kind `lost_item`, `overcharge`, `safety` o `missing_items` (solo pedidos); texto de 10 a 1000 caracteres.
  Devuelve un id de ticket; un ticket `safety` notifica a cada administrador con `safety_ticket` cuyo `ref` es el id del ticket.
- **R2.** `resolve_ticket(admin_id, ticket_id, refund_amount)`: una vez; el reembolso es 0 o más; el reembolso no puede superar todo lo cobrado por ese viaje o pedido (propinas
  incluidas) menos los reembolsos anteriores; vuelve al medio de pago original.
- **R3.** `share_trip(rider_id, ride_id)` devuelve un token para un viaje en `driver_assigned`, `arrived` o `in_progress` (`ValueError` en caso contrario); `trip_status(token)` devuelve
  `status`, `driver_name`, `vehicle_plate` y `eta_minutes` (del conductor a la recogida antes del inicio, al
  destino después), y nunca teléfonos ni emails. Después de que el viaje termina el token lanza `KeyError`.

## S. Notificaciones

- **S1.** `notifications(account_id)` lista las notificaciones de la cuenta en orden, cada una con `at`,
  `kind` y `ref`. Los tipos incluyen `ride_offer` (conductor), `ride_assigned`, `driver_arrived`,
  `ride_completed`, `ride_cancelled`, `no_driver` (pasajero), `order_accepted`, `order_ready`,
  `order_delivered`, `order_cancelled` (pasajero), `new_order` (restaurante), `order_assigned` (repartidor),
  `payout` (trabajador) y `safety_ticket` (administrador).

## T. Reportes y auditoría

- **T1.** `daily_report(admin_id, day)`: `rides_completed`, `rides_cancelled`, `ride_revenue` (suma de `charged` de los viajes completados ese día), `orders_delivered`, `order_revenue` (suma de los cobros de pedidos), `platform_revenue` (todo monto capturado ese día por viajes, pedidos y cancelaciones, menos las
  ganancias que los trabajadores generaron ese día), `cancellation_rate` (cancelados / (completados +
  cancelados) redondeado half up a 4 decimales, 0 cuando no hay ninguno). Los viajes que terminaron en `no_driver`
  no son ni completados ni cancelados. En este modelo la plataforma no les paga a los restaurantes.
- **T2.** `top_restaurants(admin_id, start, end, limit)`: restaurantes por pedidos entregados en el periodo,
  luego por ingresos de pedidos, luego por id; cada uno con `restaurant_id`, `orders`, `revenue`. Los
  restaurantes sin pedidos entregados en el periodo no se listan.
- **T3.** `audit_log(admin_id)` lista cada acción de administrador (approve, suspend, reactivate, add_zone,
  set_surge_cap, create_promo, run_payouts, resolve_ticket, cancelación de administrador) con `at`, `admin_id`, `action` (el nombre del método: `approve`, `suspend`, `reactivate`, `add_zone`,
  `set_surge_cap`, `create_promo`, `run_payouts`, `resolve_ticket`, `cancel_ride`, `cancel_order`) y
  `target` (el id afectado; el código de la promoción; el inicio de la semana en formato ISO).
- **T4.** Los reportes y la auditoría son solo para administradores (`PermissionError`).

## U. Persistencia

- **U1.** Con `db_path`, el estado se guarda en una base de datos SQLite en esa ruta. `save()` lo confirma
  (commit); `Platform.open(db_path)` restaura el estado tal como estaba en el último `save()`, todo incluido, también el reloj, los contadores, las retenciones, los temporizadores,
  los hashes de las contraseñas y las ofertas pendientes, de modo que la plataforma se comporte como si nunca
  se hubiera detenido.
- **U2.** La base de datos tiene una tabla `schema_version` con una fila; abrir una base de datos con una
  versión desconocida lanza `ValueError`.

## V. Requisitos no funcionales

- **V1.** Seguridad: ninguna contraseña en texto plano, número de tarjeta ni cvc se guarda nunca en
  estructuras en memoria expuestas por la plataforma ni se escribe en la base de datos.
- **V2.** Privacidad: los teléfonos siempre se enmascaran en los datos devueltos (A4, R3).
- **V3.** Rendimiento: con 5 000 conductores disponibles, `quote_ride` y `request_ride` tardan cada uno
  menos de 0.5 segundos.
- **V4.** Determinismo: con las mismas llamadas, la plataforma devuelve los mismos resultados; nunca lee el
  reloj del sistema ni números aleatorios, salvo las sales de las contraseñas.
- **V5.** Código: solo biblioteca estándar, Python 3.12, anotaciones de tipo en los métodos públicos,
  pruebas en `tests/`.
