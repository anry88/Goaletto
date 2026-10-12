# Каталог событий · контракт v1

Envelope/allowlist закреплены в `application.analytics` и [contract-v1.md](contract-v1.md);
collector реализован в [storage.md](storage.md); живой сбор ещё не включён. Определения метрик, временные окна
и исключения: [measurement-plan.md](measurement-plan.md).
Имена и версии фиксировать в коде до инструментирования; изменение смысла — новая версия.

| Событие | Origin | Когда фиксировать / защита от дублей |
| --- | --- | --- |
| manager_started | user | Первый принятый игровой /start; один на manager |
| campaign_touched | user | Валидное касание /start; update + effect, first-touch отдельно |
| club_offer_delivered | system | Telegram принял предложение; outbox + stage |
| club_preview_opened | user | Явный выбор превью, не автоматический показ |
| club_claimed | user | Успешный атомарный claim; claim operation |
| club_opened | user | Явный просмотр своего клуба; текущий ownership + update effect |
| lineup_opened | user | Явная команда/кнопка состава |
| lineup_saved | user | Валидный состав действительно сохранён; accepted command |
| next_fixture_opened | user | Явный просмотр календаря своего клуба |
| match_opened | user | Явный выбор собственного матча; update + effect |
| match_command_accepted | user | Валидная команда сохранена; command_id + accepted |
| match_command_applied | system | Команда реально применена; command_id + applied |
| fixture_finished | system | Единственный commit результата; fixture + finished |
| result_delivered | system | Telegram принял сообщение результата; outbox + stage |
| match_report_opened | user | Явный выбор отчёта; update + effect |
| next_action_opened | user | Явный выбор предложенного следующего действия; bounded target |
| action_rejected | user | Обработанный отказ с конечным reason_code, до rollback отдельная попытка при необходимости |
| help_delivered | system | Доставлена контекстная подсказка; outbox + stage |
| feedback_prompt_delivered | system | Telegram принял prompt; campaign + manager |
| feedback_answered | user | Кнопочная причина / отдельный restricted comment; response operation |
| feedback_skipped | user | Явный skip/opt-out; campaign + manager |
| experiment_exposed | system | Вариант реально доставлен; assignment + exposure stage |
| bot_unavailable | system | Подтверждённая недоступность доставки; bounded reason |
| club_ownership_ended | system | Прекращение конкретного периода владения; ownership + end |
| player_last_season_announced | system | Решение для полного будущего сезона сохранено; player + target season + stage |
| player_retired | system | Уход после отмеченного последнего сезона; player + finished season + retirement |
| bot_youth_replacement_created | automation | Одно молодёжное пополнение после retirement; retired player + season |

Принятая команда не означает применённую. Сохранение автосостава — system, не
lineup_saved с origin=user. Автоматические решения — origin=automation, не события человеческой активности.
Доставка и exposure не продлевают user-сессию.

manager_started начинается с игрового онбординга. Preview /start нынешнего каркаса
не относится к когорте будущего запуска. coverage_started_at и contract_version
отмечают начало сбора; исторические фиктивные шаги не достраиваются.

## Технические попытки

Отдельный поток operation_attempts: operation_id, stage, started/finished_at,
outcome, bounded failure_class и retry_number. Явные разрешённые поля; без raw exception
с секретами. FK/корреляция с command/outbox/fixture хранится в PostgreSQL, не labels.
Он не подменяет бизнес-события и не участвует в активности пользователя.

## Порядок и проверка

Нельзя полагаться только на timestamp: event_id/последовательность эффекта сохраняет
стабильный порядок при одинаковом времени. recorded_at отличает позднюю запись от
момента события. Дедупликация применяется до агрегирования; late arrivals пересчитывают
затронутые окна отчёта. Версии/неизвестные имена видны в контроле покрытия.

Справочник разрешённых user-действий общий для активности, сессий и retention.
Срок владения продлевает отдельный более узкий allowlist игровых действий того же
контракта; feedback и отклонённые команды не продлевают владение. action_rejected
измеряет ошибку, но не retention/готовность.
Добавления экономики/платежей и новые variants сначала получают контракт и критерии
приёмки. Чувствительные operational identifiers не выводятся в общие выгрузки.

## Regulation lifecycle · AND-52

System events: match_lineup_locked, fixture_started, match_tick_committed,
match_second_half_started, fixture_finished, fixture_abandoned. Требуют fixture_id;
operation UUID закреплён за fixture/stage/tick/version. Не образуют human activity.
Command-applied дополнительно требует command_id. Persisted operation_attempts
отделены от journey и коррелируются через job/fixture/operation; delivery success
не возникает от постановки outbox.

User start_accepted фиксирует каждый принятый игровой /start; manager_started
остаётся единственным первым входом. known campaign_touched — отдельный effect.
Direct/unknown/known и first-touch хранятся в [acquisition](acquisition.md).
