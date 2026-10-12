# Результат и таблица · AND-56

Реализовано транзакционное начисление3/1/0: immutable MatchResult receipt и оба
Standing обновляются внутри транзакции финального тика до outbox результата.
Receipt связан с парой Match/Fixture composite FK, исторической league/season
membership и canonical final state hash. Повтор не начисляет очки; изменённый
checkpoint/fixture/date отклоняется. Standing locks берутся по club UUID в одном
порядке. Match finalization не делает отдельный commit или сетевые вызовы.

`LeagueStanding` и `rank_league` pure: очки, разность мячей, забитые, сохранённый
предсезонный жребий. Нужны ровно10 клубов и жребии1..10 без дублей; нет новых
случайных сортировок. `league_table` читает историческую SeasonClub membership,
не выводит старую лигу из нынешнего Club.league_id.

Abandoned не начисляет очки/3:0 и требует отдельного административного решения.
Для исправления пропущенного finished result есть идемпотентный handler
match.finalize_result; он проверяет полный90′ checkpoint и использует ту же receipt.

Telegram presenters показывают таблицу по5 клубов, граничные promotion/relegation
маркеры и компактный отчёт. UUID callbacks закодированы base64url, укладываются
в64 байта, parser проверяет canonical encoding; HTML имена экранируются.
Presenter не даёт authorization: будущий handler обязан проверять manager/ownership.

**AND-56 остаётся In Progress:** реальные кнопочные handlers отчёта/таблицы ещё
не подключены к регистрации/навигации (AND-47/48). Код presenter не выдаётся за
готовый игровой интерфейс. История/расширенные статистические отчёты — AND-66.

Проверки: pure3/1/0 и tie-break ordering, SQL-recording finalize/retry/changed result,
row-lock order, abandoned guard, paging/callback length/HTML escape; offline0006 DDL.
Ни тестовая, ни production БД не использовалась. Реальные PG locks/rollback/triggers
не проверены. Runtime integration сохранения финального тика проверена с supplied
objects/recorded SQL, а не PostgreSQL эмуляцией.

## История и архив · AND-66

Read-only queries `application/history.py` поверх сохранённых данных:
receipts MatchResult, документы событий MatchTick, чекпоинты Match
(замороженные имена/возрасты/формы стартового снапшота) и исторические
memberships SeasonClub с хранимыми счётчиками Standing. Очки не пересчитываются
и не начисляются: порядок таблицы — pure rank_league по хранимым счётчикам,
как в финализере результата. Новых производных показателей (MVP и т.п.) нет: им нужна
утверждённая формула.

Telegram: presenters `bot/history_cards.py` + callbacks `hx:` + роутер
`history_router.py`. Текст отчёта — счёт из receipt, владение/удары/угловые из
терминального состояния (та же подача, что result_view), голы/замены/карточки/
травмы из сохранённых событий, составы с замороженными возрастами. Таблица
архива переиспользует table_view из result_views.py. Живые переименования клуба
и смена лиги архив не переписывают: отчёт берёт имена из снапшота, архив — tier
из SeasonClub. Каждый экран перепроверяет текущее владение и пишет game-action
receipt (club_opened / match_report_opened / lineup_opened); analytics opt-out
receipt не отменяет. Бывший владелец получает отказ, а не данные. Пагинация —
стабильный порядок (время, fixture id), отчёт открывается повторным чтением
строк, без памяти процесса.
