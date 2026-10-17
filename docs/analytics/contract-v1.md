# Контракт событий v1 · AND-73

Дополнение 2026-10-18: подтверждения direct Telegram responses для
club_offer_delivered/help_delivered используют существующие operation_id,
effect_number=31001 и ограниченный target onboarding/squad вместо outbox_id.
Они фиксируются после принятия Telegram, не являются user activity.
Исторические outbox envelopes и смысл delivered сохраняются; новых полей нет.
[Приёмка и ограничения](../launch-measurement.md).

Статус: контракт реализации закреплён в `application/analytics.py`, envelope/allowlist
и временные границы проверяются без БД. Транзакционный collector, sessions,
dedupe/exclusions и retention helper реализованы: [storage.md](storage.md).
Продуктовые SQL-отчёты/опросы ещё в плане; реальная collection не включена. Preview-бот не создаёт launch cohort.

## Формат и версии

`JourneyEvent` — неизменяемый envelope без свободного JSON/текста: UUID event_id,
name из `EVENT_SPECS`, version=1, timezone-aware UTC occurred_at/recorded_at,
origin, operation_id, effect_number 0–32767 и surface из конечного списка.
Идентификаторы: analytics_manager, session, campaign, feedback_campaign, experiment,
club, ownership, fixture, command, outbox, person, season. Дополнительно разрешены
конечные reason, target и experiment variant control/a/b. campaign_id относится к
атрибуции привлечения, feedback_campaign_id — к предложению опроса.

Имена людей, Telegram ID, username, raw /start, callback_data, message, exception,
платёжные реквизиты и комментарий отсутствуют в envelope. Их нельзя передать как
произвольные properties. `operation_id` и остальные корреляции остаются restricted,
не метками Prometheus. Все обязательные ссылки и origin задаёт именованный EventSpec.
Это общий validation contract application, не импорт в чистый domain.

Dedupe: `journey:v1:{operation_id}:{effect_number}`. Operation ID создаётся один раз
для принятого update/business operation и сохраняется PostgreSQL; эффект имеет
стабильный номер. Новый retry/event_id не меняет ключ. Для manager_started operation
закрепляется за первым принятым игровым /start, а не каждым новым update.
Worker связывает operation с fixture/stage, retirement/person/season либо outbox/stage.
События command accepted/applied имеют разные effects и общий command reference.

Смысл, обязательные поля или правила активности меняются только новой версией.
Сначала код и SQL reader поддерживают старую и новую версии, затем writers переключаются.
Старые rows не получают новое значение задним числом. Неизвестная версия/имя — ошибка
валидации и диагностика покрытия, не тихо отброшенная запись. Изменение добавляется
в каталог и продуктовые примеры. До подключения AND-74 неизменяемый envelope не
должен выдаваться за надёжный сбор данных.

## Активность и шесть месяцев

Точный список находится в `EVENT_SPECS`; `club_opened` добавлен для явного просмотра
собственного клуба. Повторный /start владельца может давать club_opened, но не второй
manager_started. Для внешнего менеджера активность создают успешные user-события,
включая feedback. `action_rejected` сохраняет попытку/ошибку, но не готовность,
retention или обновление сессии. Внутренние аккаунты исключаются по operational flag.

Срок владения продлевают только club_claimed/club_opened, lineup_opened/lineup_saved,
next_fixture_opened, match_opened/match_command_accepted/match_report_opened — после
проверки текущего ownership. Превью чужого клуба, campaign touch, feedback, navigation
next_action сами по себе не продлевают срок. Открытие целевого игрового экрана даёт
его отдельный effect. Доставка, tick, applied command и автоматика не user activity.
Будущие рынок/тренировка/финансы должны получить отдельные именованные события до запуска.

Успешный user envelope требует analytics_manager_id/session_id; action_rejected
может иметь NULL session_id и не создаёт/обновляет сессию. Background envelope запрещает
session_id; system/automation не продлевают сессию. Operator actions идут в отдельные
operational attempts; v1 не переименовывает их в человеческие product events.
Новая сессия начинается при разрыве ≥30 минут между явными успешными user-действиями.
Длина сессии не является временем просмотра live-матча.

Telegram accepted означает только успешный ответ API. result_delivered/offer/help/
prompt/exposure не означают чтение. Чтение без кнопки остаётся неизвестным;
match_report_opened/match_opened — явное действие. Crash после send, до commit —
operation attempt outcome_unknown, до reconciliation не логический delivery success.

## Три KPI и зрелость

Все интервалы `[начало, конец)`, UTC; T — зафиксированное время отчёта.
Повторные updates/effects сначала дедуплицируются; n/N считает distinct manager,
а матчевые действия относятся к конкретным fixture и ownership. Late arrivals
пересчитывают затронутые окна, recorded_at не подменяет occurred_at.

| KPI | N | n |
|---|---|---|
| Готовность за 24 часа | Внешние manager_started, у которых started+24h ≤ T | Успешный claim и собственноручный lineup_saved для этого владения внутри первых 24h |
| Первый доступный матч | Внешние владельцы с завершённой первой возможностью и actual_finish+24h ≤ T | match_opened, match_report_opened или match_command_accepted своего fixture в [actual_start−24h, actual_finish+24h) |
| Возвращение ко второму | Участвовавшие в первой возможности, у которых вторая завершена и её окно закрылось | Такие же явные действия во второй возможности |

Возможность — завершённый матч лиги собственного клуба, ownership создан до или
в момент lineup lock и сохранялся до конца окна. Claim после lock пропускает fixture.
Прерванное владение показывается отдельно и исключается из этих N; это не успех
нового владельца. Боты, internal, cancelled fixtures и незавершённые матчи исключены.
Если календарь неизвестен/ещё нет возможности, показать waiting/coverage, а не ноль.
Перенос/пауза используют actual times; кубок — отдельный маршрут. Возврат через
неделю не заменяется дневной метрикой.

Dk, k∈{1,3,7,30}: n — менеджеры с хотя бы одним разрешённым user effect в
[started+k×24h, started+(k+1)×24h), N — внешние starts с полностью закрытым окном.
Границы реализуют `retention_window`, `match_activity_window`, `window_is_mature`.
SQL реализация и строгие проверки числителей/знаменателей остаются AND-79/82.

## Проверяемые продуктовые примеры

- T=12 октября 12:00 UTC. Три внешних starts 11 октября 12:00 созрели; четвёртый
  11 октября 13:00 ещё нет. Один взял клуб и сам сохранил состав за 2h, второй только
  получил автосостав, третий остановился до claim. Готовность 1/3; если первый нажал
  сохранение дважды, результат всё ещё 1/3. Internal start не даёт четвёртый N.
- Четыре владельца имеют завершённую первую возможность с закрытым окном.
  Один открыл отчёт, второй отправил валидную команду, третий только получил результат,
  четвёртый молчал. Участие 2/4. accepted→applied не даёт второе действие человека.
- Из этих двух участников лишь у одного закрыто окно второго матча; он открыл
  табло. Возвращение 1/1, один waiting. Делить на двух было бы преждевременно.
- Старт 11 октября 12:00: D7 — [18 октября 12:00, 19 октября 12:00). Действие
  18 октября 11:59 не D7; ровно 19 октября 12:00 уже за пределом. Отчёт 19 октября
  11:59 не включает этого менеджера в N. Result delivered не даёт D7.
- Матч перенесён на 18 октября 18:00 и закончился 18:33: окно до 19 октября 18:33,
  при T=18 октября 20:00 оно ещё незрелое. Чистое ожидание не является причиной ухода.
- Feedback после 31 августа не продлевает ownership. Игровой просмотр 31 августа
  23:42 UTC продлевает до 28 февраля (29 в високосном году) в 23:42, не на 180 дней.

## Покрытие, доступы и удаление

Перед первым внешним стартом AND-74 создаёт persisted coverage_start, version и
настройки retention. Coverage не начинается сегодняшними preview/demo effects;
исторические отсутствующие действия не достраиваются. В отчёте показывать дату,
версии и gaps. Текстовые причины ухода собираются только добровольно в AND-78.

Рабочие стартовые настройки до подключения сбора: подробные journey events/attempts
180 дней, отдельные добровольные комментарии 30 дней, экспортные агрегаты без
индивидуальных refs 24 месяца. Настройки конфигурируемы и versioned; это операционная политика проекта.
Collector cleanup helper реализован, автоматическое расписание ещё AND-82;
сроки комментариев/экспортных агрегатов остаются будущими AND-78/79.
Telegram mapping нужен operational bot; доступ к нему и к комментариям отдельно от
агрегатного Grafana. Product exports используют псевдоним; имена/Telegram IDs не выводят.
В обычных логах только конечные коды и агрегаты, без envelope/комментария целиком.

Удаление аналитики по запросу: найти restricted mapping → стереть связанные
journey/feedback/assignment records и mapping → исключить из будущих индивидуальных
отчётов; fictional players, матчи и история клуба не являются менеджерской аналитикой.
Политика backups/повторных импортов должна не восстанавливать удалённое в выгрузках.
Очистка по возрасту данных, роли доступа и сценарий удаления входят в AND-74/78/82.
Они должны быть проверены до привлечения, сейчас не выполняются.

Основа: [план измерений](measurement-plan.md), [каталог](event-catalog.md),
[схема данных](../data-model.md). Контракт закрывает AND-73; сбор данных — AND-74,
источники — AND-75, instrumentation — AND-76/77, SQL — AND-79.
