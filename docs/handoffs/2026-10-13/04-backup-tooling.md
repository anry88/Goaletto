# D · Инструмент резервного копирования без деплоя

Ты внешний разработчик Goaletto — Python-футбольного менеджера в Telegram.
Репозиторий: https://github.com/anry88/Goaletto (закрытый, лицензию не менять).
Работай только над указанным подпакетом и передай готовый код Codex для review.
Не запускай других агентов и не отправляй сообщения от имени пользователя.

Перед началом:
- Прочитай AGENTS.md, README.md, docs/game-rules.md, docs/match-clock.md,
  football-manager-bot-gdd.md и документы ниже. Архивный GDD v1 не источник правил.
- Получи актуальный origin/main и создай отдельную ветку codex/<имя-задания>.
  Запиши исходный SHA. Не продолжай старую агентскую/integration-ветку.
  Игровой код на момент подготовки проверен на 6863b109; это справка,
  а не разрешение начинать новую ветку с устаревшего commit.
- Сначала проверь текущую реализацию: исторические статусы в документах/Linear
  могут отставать. Не реализуй повторно уже работающую функцию.

Общие границы:
- Только Python для приложения, Telegram, PostgreSQL. Без Mini App, JavaScript,
  нового публичного API, LLM в симуляции и новых игровых правил.
- Domain без aiogram/SQLAlchemy/сети/часов; время и seed передаются явно.
- Обычный матч: 30 тиков по 60 секунд, 3 игровых минуты на тик,
  перерыв 180 секунд, всего 33 реальные минуты плюс зафиксированные остановки.
- Изображения/портреты/каталоги/appearance IDs не менять и не генерировать.
- Не трогай файлы вне своего списка. Нужное изменение общей границы оформляй
  отдельным точным integration.patch и объясняй его. Исключения перечислены в задании.
- PostgreSQL владеет durable состоянием. Не подменяй его FSM/памятью процесса.
  Системные события и доставка не продлевают владение клубом.
- Не запускать настоящие polling/sender, не использовать Telegram-аккаунты,
  не трогать HDC/соседние сервисы, не сбрасывать рабочую БД.
  Новые локальные/CI тестовые БД и PostgreSQL services запрещены.
  PG-тесты можно написать; исполнение на единственной Goaletto DB выполняет Codex
  последовательно после сохранения мира. Отсутствие доступа не заменять SQLite.
- Не читать/копировать секреты в результат; не печатать токены, DSN, raw exceptions
  с credentials, тексты приватных сообщений. Используй fake transport/runner.
- Тексты игры RU/EN; без технических ID, версий, тиков и объявлений о твоих изменениях.

Сдача:
1. Commit/patch или PR, исходный SHA, список изменённых файлов.
2. Реальное поведение и границы подпакета; никаких обещаний готовности целого MVP.
3. Ruff check, ruff format --check, mypy и релевантный pytest. Для миграций —
   offline SQL upgrade/downgrade и пояснение схемы. Честно отдели passed от skipped.
4. Короткий воспроизводимый сценарий приёмки Codex и оставшиеся ограничения.
5. Не merge/push main и не закрывай Linear. Codex проведёт review, PG/native проверки,
   сведёт принятую работу в main и обновит задачи по фактически выполненному объёму.

## Задание

Связь с Linear: backup tooling часть AND-70.
Ветка: codex/backup-tooling.

Результат: Python-инструмент для получения проверяемого pg_dump Goaletto и
runbook передачи/восстановления. Настоящие dump/restore и установка расписания
остаются приёмкой Codex. Не дублируй уже готовый read-only diagnostics CLI.

Прочитай docs/deployment-hdc.md, docs/runtime-diagnostics-runbook.md,
compose.yaml, compose.local.yaml, deploy/compose.hdc.yaml, deploy/compose.release.yaml.
У Goaletto локальный PostgreSQL на5433;5432 занят другим проектом.
На HDC внутри собственного Compose сеть/порты могут отличаться — нельзя
подменять target только по номеру порта.

Сделай scripts/backup_goaletto.py и docs/backup-runbook.md:
- явная конфигурация target/output, default dry-run, явный --apply для dump;
  dry-run показывает безопасный план, без пароля/credential DSN;
- до dump проверяется имя БД goaletto и явно согласованный target/project.
  Не делай перечисление/backup всех баз или общих Docker volumes;
- PostgreSQL custom-format archive во временный файл, атомарное завершение,
  SHA256 manifest, UTC время, версии инструмента/PostgreSQL, размер;
  при ошибке не оставляй файл, похожий на успешный backup;
- минимальные права файлов, отказ перезаписывать готовый архив молча;
  одинаковый timestamp не даёт коллизии или повреждения;
- subprocess без shell=True, секреты не в argv/логах/manifest;
  stderr внешней команды не печатается целиком, ошибки — безопасные классы/коды;
- subprocess/environment/file I/O можно подменять для unit tests.
  Tool не копирует .env и не отправляет архивы третьим сторонам.
- Runbook для off-host копии, checksum проверки, остановки только Goaletto
  процессов, совместимости миграций, rollback/restart и проверки после restore.
  Retention/ежедневное расписание — явные операторские параметры/пример,
  не автоматическая установка cron/Task Scheduler.

Не реализуй destructive restore команду в этой волне. Пользователь запретил новые
local/CI test DB: старый пункт Linear про isolated DB не разрешает их создавать.
Codex согласует окно и проверит restore на единственной development Goaletto DB
с backup текущего мира. Ни HDC, ни реальные dump/удаление/restore здесь не запускать.

Владение файлами: scripts/backup_goaletto.py,
tests/unit/test_backup_tooling.py, docs/backup-runbook.md.
Не менять Compose/deploy files, credentials, production services, CI или pyproject.

Приёмка: fake успешный dump, failed dump, interrupted/partial file, collision,
wrong database/target, отсутствие pg_dump, ошибки прав, checksum manifest,
отсутствие секретов в captured stdout/stderr/argv. Проверяемая готовность к
реальному drill, а не заявление, что backup уже восстановлен или HDC настроен.
