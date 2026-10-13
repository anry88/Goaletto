# Добровольный отзыв · AND-78 (часть A, ручной/internal)

Ручной добровольный отзыв после результата. Только internal/test cohort;
внешним пользователям ничего не рассылается и не включается.

## Вход

- Вторичная кнопка «Оставить отзыв» на финальном экране `/match`
  (полный тайм/остановка). Основной CTA — отчёт о матче — сохраняет приоритет.
- Ручная команда `/feedback` (меню команд).
- После команды проверяется действительный менеджер и internal/test eligibility;
  Telegram allowlist не расширяется.
- Ручной вход не обещает доставку: сообщение считается доставленным только
  после подтверждения транспорта.

## Правила

- Один prompt на менеджера/кампанию (`manual-v1`); общий cooldown минимум 7 дней
  (`feedback_cooldown_days`, `ge=7`).
- Повторный callback не даёт второго response/события (статус terminal + детерминированные
  operation UUID + UNIQUE).
- Skip/opt-out terminal; недоступный бот (`bot_available=false`) исключён.
- Причины из согласованного списка (7): непонятно что дальше; сложно настроить состав;
  долго ждать матч; неудобное время; неинтересная игра; техническая проблема; другое.
  RU/EN, кнопки ≤64B, устойчивый scope `fb:*`, без техподробностей.
- Комментарий только после явного выбора причины: окно 30 минут, максимум 500 символов,
  можно пропустить. Маршрут не поглощает `/команды` (фильтр `~startswith("/")`)
  и обычные сообщения вне окна (молчаливый return, без ответа).
- Durable session в PostgreSQL (`feedback_responses`), не только FSM.
- Повтор доставки/update безопасен (dedupe по `(manager,campaign)`, `operation_id`,
  существующему комментарию).

## Хранение и события

- Свободный текст — только в `feedback_comments` (restricted), никогда в
  `JourneyEvent` envelope, логах, exception text, labels или общем dashboard.
- Используются существующие `feedback_answered` / `feedback_skipped` /
  `feedback_prompt_delivered` по точному контракту v1. Feedback — явная
  аналитическая активность, но не продление шестимесячного владения.
- `Accepted/sent/unknown` не смешиваются: сеть вне DB-транзакции; запись доставки
  — отдельной идемпотентной транзакцией только после подтверждения транспорта.
  Неизвестный исход не записывается как доставка.
- Отдельный обязательный параметр `feedback_comment_retention_days` включает сбор.
  Без него `open_prompt` отклоняется (`FeedbackDisabled`). Политика по умолчанию
  не задана сознательно.
- `cleanup_comments(at, retention_days)` идемпотентен, удаляет только комментарии
  старше границы (строго `<`, ровно на границе сохраняются), игровые данные не трогает.
- Analytics opt-out уважается: `opt_out` стирает комментарии менеджера и создаёт
  tombstone через `exclude_manager(..., "opt_out")`; receipts/cooldown
  (`feedback_responses`) не зависят от удаления событий.

## Конфигурация

```bash
GOALETTO_FEEDBACK_ENABLED=true
GOALETTO_FEEDBACK_COMMENT_RETENTION_DAYS=30
GOALETTO_FEEDBACK_COOLDOWN_DAYS=7
```

`feedback_enabled=false` или отсутствие retention отключает новые prompts,
но сохраняет чтение terminal-состояний. Cooldown меньше 7 отклоняется конфигом
и application-валидацией.

## Что НЕ входит

- Автоматические prompts через 24ч незавершённого входа и после пропущенной
  зрелой матчевой возможности (ждут центральную opportunity instrumentation).
- Запуск кампаний/sender в worker; transport interface не меняется.
- Вопрос «почему ушёл» при чистом ожидании календаря.

## Схема (0018)

- `feedback_campaigns(slug unique)` — реестр кампаний, append-only.
- `feedback_responses(manager_id, campaign_id unique, operation_id unique,
  status prompted/answered/skipped/opted_out, bounded reason, prompted/responded/comment_due)` —
  durable сессия, индекс `(manager_id, prompted_at)` для cooldown.
- `feedback_comments(response_id PK/FK CASCADE, created_at indexed, text 1..500)` —
  restricted текст, удаляется cleanup и opt-out.

Downgrade требует пустых feedback-таблиц, иначе исключение.

## Приёмка Codex

1. Internal `/feedback` → prompt; повтор → тот же response, без второго события.
2. Чужой/протухший callback → недоступный экран, без мутации.
3. Причина → комментарий (≤500, ≤30мин) или пропуск; команда внутри окна не перехвачена.
4. Skip/opt-out terminal; opt-out стирает текст и блокирует новые prompts.
5. Cooldown 7д: второй slug в окне отклоняется.
6. Restart/rollback: retry той же операции идемпотентен.
7. Ownership deadline не двигается; текст отсутствует в journey envelope/логах.
8. RU/EN, кнопки ≤64B.
9. PG-тест `tests/integration/test_feedback_pg.py` выполняет Codex на единственной
   Goaletto DB после сохранения мира; локально не запускать reset.

AND-78 целиком не закрывается этой частью.
