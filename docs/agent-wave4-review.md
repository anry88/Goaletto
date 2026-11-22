# Четвёртая внешняя волна · приёмка 2026-10-13

Общая база main555f3637. Приняты внешние commits:

| Пакет | Commit | Что входит |
|---|---|---|
| A feedback | 216ff318 + b498431a | Ручные отзывы для internal cohort, durable comments, migration0018 |
| B player-season-plan | a3c872b8 | Pure plan age/last-season/retirement с retry receipt |
| C PostgreSQL races | fc922f94 | 6 новых interleavings рынка и sender |
| D match-opportunities | 872d3a08 | Pure first/second opportunity KPI с явным coverage |

Ветки сведены без конфликтов. При review Codex исправил:

- Campaign insert теперь conflict-safe между разными менеджерами.
- Доставка prompt хранится в operational delivered_at: internal exclusion не
  должен делать подтверждённый Telegram send недоступным для dedupe.
- Opt-out очищает reason при terminal transition, согласуя CHECK схемы;
  комментарии удаляются, tombstone сохраняется.
- Skip комментария закрывает durable window и проверяет manager/response scope.
  Обычные сообщения вне окна идут дальше через SkipHandler; команды не поглощаются.
- Кнопка feedback видна только eligible internal manager при включённой функции
  без уже созданного prompt. Основная кнопка отчёта остаётся первой. Меню/help RU/EN.
- Пользователь утвердил retention30дней. Worker создаёт singleton durable
  feedback.cleanup:v1 и переносит её на сутки после успешного удаления;
  cleanup не удаляет responses/cooldown/игровые данные.
- Season-plan отклоняет повреждённый receipt с integer вместо boolean outcome.
  Last-season до32 запрещён. Вероятности остаются 5/10/20/35/55/80/100%.
- Opportunity calculator учитывает один fixture для двух клубов; законченный
  до lineup lock ownership не допускает будущие матчи. Действия до claim
  не засчитываются. При переносе порядок задаётся фактическим start либо
  актуальным scheduled_start. Conflicting duplicate effect отклоняется.
- Serialized report проверяет reconciliation всех групп и rate; NaN отклоняется.
- В test fixtures исправлены новый operation_id для новой кампании, совпадение
  occurred_at у duplicate effect, ожидание коммита ставки перед повтором close.
  Race с close-job теперь реально захватывает job lock. Fake send нового sender
  выполняется после preflight, снаружи DB transaction.

Код чистых компонентов не подключает rollover к worker и не создаёт production
KPI из синтетических данных. Staff/youth lottery и генерация изображений не менялись.

## Проверки и локальный rollout

Ruff check/format319, mypy116; **586 unit/offline passed**, 70 integration
сценариев deselected в финальном unit-прогоне. Полный настоящий PG-прогон дал
68passed/2failed (ошибки fixture/synchronization выше), затем весь feedback/race
подпакет — **12passed**. Итого подтверждены **70 уникальных PG-тестов**;
это полный прогон и целевой повтор, а не один зелёный запуск всех70.
После restore два read-only diagnostics PG-теста повторно прошли без reset.
Migration0018: offline upgrade/downgrade SQL, online downgrade/upgrade на пустых
feedback tables и alembic check успешны; после restore upgrade/check повторены.

До reset остановлены известные процессы Goaletto; подтверждены target
localhost5433/goaletto,2internal managers/0external,50clubs/50results,initial seed.
Реальный custom backup1 847 860B с manifest/SHA256 сохранён в ignored work/.
После PG весь исходный мир восстановлен из него; миграция0018 применена поверх
восстановления. Новых DB нет. Auto-review один раз отклонил запуск по старому
снимку до restore; повторный preflight подтвердил исходный мир, затем запуск
был разрешён. Сейчас работают один бот и один worker; в логах нет ERROR/Traceback.

Native Telegram на тестовом аккаунте Sputnik: /match показал secondary feedback
после кнопки отчёта; кнопка открыла prompt, причина «Другое» открыла comment window.
/help внутри окна сработал, тестовый текст сохранён один раз, повторный /feedback
дал thanks без второго response. English проверен через /settings и /feedback,
затем RU восстановлен. Проверено operational delivered_at и отсутствие
feedback journey events для internal account; один тестовый комментарий хранится
только отдельно. Это synthetic internal smoke, а не отзыв реального игрока.

На host включены GOALETTO_FEEDBACK_ENABLED=true, COMMENT_RETENTION_DAYS=30,
COOLDOWN_DAYS=7 без изменения Telegram allowlist. Работает одна pending cleanup
job. Продления ownership от feedback нет (PG assertion и native settings deadline
после отзыва совпал с последним /match). production rollout не выполнялся: игра всё ещё
в закрытом локальном тесте, общая seasonal integration и production gates впереди.

Следующие работы:
1. Codex: общий durable rollover — сезонный lock, итоговые таблицы/переходы,
   player-season receipts, отмена лотов/резервов, календарь и предупреждения.
   До исполнения подтвердить покрытие уникальными внешностями для новых людей;
   текущий запас свободных player appearances исчерпан, молчаливый reuse запрещён.
2. Внешний bounded подпакет: read-only SQL projections для нового opportunity
   calculator + SQL/hand-count parity + узкое CLI подключение. Без новых events,
   schema/jobs и ложного complete coverage при retention gaps.
3. Внешний bounded подпакет: operator feedback aggregates — число prompts,
   delivered/unknown, answered/skip/opt-out и кнопочные причины с n/N.
   Свободный текст не экспортировать; внутреннюю тестовую когорту показывать явно.
4. Затем новый тестовый тур двух аккаунтов с отзывом и контроль новой интеграции
   rollover отдельно от настоящего недельного календаря. production rollout после
   закрытия production gates, backup/off-host/reboot и эксплуатации.

Это рекомендации следующей работы; дополнительные агенты сейчас не запущены.
