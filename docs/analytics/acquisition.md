# Кампании и источник входа · AND-75

Обновление 2026-10-18: [игровое подключение и PG-приёмка AND-75/76/79](../launch-measurement.md).
Описание прежней внешней поставки ниже — история; текущие SQL last-steps/recovery/
match-opportunities подключены к операторскому CLI. Технические latency и
автоматические опросы остаются отдельными задачами.

Реализованы opaque start-коды, registry source/placement/publication date,
транзакционный `accept_start` и immutable first-touch до получения клуба.
Здесь наблюдается принятый /start; переход по ссылке без /start неизвестен.
[Ограничения Telegram deep links](https://core.telegram.org/bots/features#deep-linking):
до64 символов A-Z/a-z/0-9/_/-. Generated code24 chars; URL для GoalettoBot.
Нет redirect/frontend/web API. Код не содержит Telegram ID/личные данные.

`classify_start`: absent/empty → direct; зарегистрированный точный code → known;
unknown/невалидный code → unknown. Сырое содержимое неизвестного параметра не
сохраняется. Registry immutable; изменение source/placement не переименовывает
исторические кампании. Repeat start сохраняет touch, но не переписывает first-touch
и не создаёт новый manager_started. Telegram update → стабильный operation UUID;
retry не обновляет session/не повторяет эффекты. Pseudonym до claim, manager lock,
first-touch/touch/journey в одной caller-owned transaction.

Internal/test/operator/opt_out не получают продуктовую attribution. Opt-out cascade
стирает связанные first-touch/touch rows. Retention helper удаляет touch и first-touch
по заданному сроку180д; first_game_start_at предотвращает новую launch cohort после
очистки. Новые returning touches допустимы, новый фиктивный первый источник — нет.

Source funnel SQL возвращает starts, eligible24h, waiting, claimed/ready внутри первых
24ч. Ready требует claim+lineup_saved того же ownership, origin=user, не автосостав.
Внутренние исключены. Report metadata содержит coverage_started_at/retention clipping;
не выдаёт отсутствие старых данных за нулевую конверсию. Проверяются закрытые поля
агрегата, неотрицательные counts, waiting+eligible=starts и ready<=claimed<=eligible.
Деноминатор: eligible, не starts с ещё незрелыми менеджерами.

Источник — связанный принятый код, не доказательство рекламного клика/показа.
CTR/CAC не считаются без независимых данных показов/кликов/расходов. Индивидуальные
refs и Telegram mapping не выводятся операторским CLI. SQL выполняется restricted
read transaction, внешний экспорт отдельный. Реальные роль/GRANT проверки — AND-82.

```bash
python -m goaletto.application.campaign_cli create --source community --placement launch \
  --published-on 2026-10-12
python -m goaletto.application.campaign_cli report --cohort-from 2026-10-12T00:00:00Z \
  --at 2026-10-13T00:00:00Z
```

Без --apply нет подключения к БД/.env. --apply create пишет registry, report читает
реальные aggregates; здесь он не запускался. Код alias goaletto-campaign после reinstall.
Миграция0007 не регистрирует кампании и не создаёт пользовательские когорты.

**AND-75 принят 2026-10-18:** настоящий игровой /start, immutable source,
повтор Update, claim с preview и без него, ручная readiness и SQL source funnel
проверены на PostgreSQL с fake Telegram. Internal/opt-out, поздние записи,
границы окон и retention проверяются общей матрицей. Production polling/рассылка
не запускались. Полный gate доступов/наблюдаемости остаётся AND-82.
