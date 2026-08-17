# Design: тестовый Telegram — волна 3 (группы и каналы)

Дата: 2026-08-16
Статус: согласован в brainstorming
Зонтик: [`2026-08-13-test-telegram-design.md`](2026-08-13-test-telegram-design.md) §11 волна 3
Планы волн 1–3 остались в приватном репозитории происхождения и сюда не переехали.
Правила Bot API: [Available types](https://core.telegram.org/bots/api#available-types), [Privacy Mode](https://core.telegram.org/bots/features#privacy-mode), [Bots FAQ](https://core.telegram.org/bots/faq)

Пошагового плана здесь нет. План — отдельный файл после ревью этой спеки.

## 1. Зачем

После волн 1–2 `tgmock` — сеть private-чатов плюс веб-клиент. Group/supergroup/channel в ядре нет: `Chat.type` всегда `private`, участники не существуют, отрицательный id либо outbound-сахар e2e, либо 403.

Волна 3 добавляет групповые чаты так, чтобы бот на `TELEGRAM_API_BASE` вёл себя как на `api.telegram.org` в пределах админского минимума: privacy, `ChatMember*`, `my_chat_member` / `chat_member`, пост в канал от бота-админа, лимит 20/мин на группу.

Вынос в отдельный репозиторий (волна 1.5) снова отложен. Код остаётся в `tgmock/` этого репо.

## 2. Границы

**В волне**

- Три `Chat.type`: `group`, `supergroup`, `channel`. Клиент «Группа» создаёт `supergroup`.
- Сущность чата только для них. Private остаётся парами `bot_chats` / `private_user_chats`. Outbound e2e (`ensure_outbound_chat`, `ADMIN_CHANNEL_ID = -1001234567890`) не становится группой.
- User API создаёт чат, двигает участников и ставит/снимает админа. Admin — тот же сахар для сидов. Bot API `promoteChatMember` — для бота, которого человек уже сделал админом.
- Bot API: `getChat`, `getChatMember`, `getChatAdministrators`, `getChatMemberCount`, `leaveChat`, `banChatMember`, `unbanChatMember`, `promoteChatMember`. `sendMessage` / edit / callback учат group/channel. `getUpdates` и `setWebhook` читают `allowed_updates`.
- Privacy — **подмножество** [Privacy Mode](https://core.telegram.org/bots/features#privacy-mode): что входит и что сознательно нет — §6. Бот-админ в чате видит все сообщения людей. В клиенте ленту видят все активные участники. Настройка `privacy_mode` — как `/setprivacy` у BotFather (`POST /admin/bots/privacy`).
- Клиент: формы группы/канала, панель участников, при добавлении бота — чекбокс «админ», в канале композитор только у тех, кто может постить. Кнопок promote/ban в UI нет.
- Reply-клавиатура: запись и чтение одним правилом. Private → ключ `(user_id, bot_id)` на write (`sendMessage`) и на read (`GET …/messages`, `UserClient.screen()`, `reply_keyboard`). Группа → ключ `(user_id, chat_id)` **для каждого** активного участника, если у разметки нет `selective` (selective не делаем). Хвост волны 2.
- `400 chat not found` vs `403 can't initiate` на private — сделано; закреплено в `tests/test_errors.py`.

**Не в волне**

Инвайт-ссылки, pin, restrict, default permissions, anonymous-админы, discussion у канала, username канала, форумы, `migrate_to_chat_id`, полный `ChatFullInfo`, inline-режим, сообщения других ботов, бот как `member` в канале, Playwright как блокер, перевод потребителя на группы, вынос репо, `telegram-paid-broadcasts`.

Неизвестный метод по-прежнему 404 боту + журнал Admin.

`getChat` в этой волне отдаёт объект `Chat`: для group/supergroup/channel — `{id, type, title}`; для private — `{id, type, first_name}` и опционально `username` / `last_name`, **без** `title`. Это не полный `ChatFullInfo` (обязательные `accent_color_id`, `max_reaction_count` не выдумываем). `getMe` не обрастает полями волны 4 (`supports_inline_queries` и т.п.).

## 3. Архитектура

Private не переписываем. Рядом:

```
Network.chats: dict[int, ChatRecord]
```

`thread_for` / `chats_for`: отрицательный id, который есть в `chats`, — этот чат. Иначе — прежняя логика пар.

Резолв исходящего Bot API (`_outbound_message`):

1. `chat_id` в `chats` → групповой путь (права, privacy на приём не влияет на отправку).
2. Уже есть private/outbound тред (`bot_chats` / `ensure_outbound_chat`) → как сейчас.
3. Человек есть в `users`, диалога не было → 403 `can't initiate`.
4. Иначе → 400 `chat not found`.

Групповой путь **не** вызывает текущие `send_text` / `append_bot_message` / `message_for_viewer` как есть — они заточены под private и сломают сеть:

- `send_text`: если `peer_id` в `Network.chats` — писать в `ChatRecord.messages`; если `peer_id < 0` и нет в `chats` — 400, **не** `create_user`.
- `message_for_viewer`: для `ChatRecord` поле `chat` = `{id, type, title}`, не `chat_of(user)`.
- SSE/`emit`: всем со статусом `creator` / `administrator` / `member`, `peer_id = chat_id`, `chat` = карточка чата. Пост канала без `from` — в клиенте подпись из `sender_chat.title`.
- `append_bot_message`: больше не `setdefault` на `chat_id < 0`. Outbound только после `ensure_outbound_chat` (фикстура e2e уже зовёт `POST /admin/outbound-chats` до первой отправки). Неизвестный отрицательный id без `chats` и без пары → 400 `chat not found`. Существующая outbound-пара (`-1001234567890`) → 200 в `bot_chats`, не в `chats`. Автосоздание opaque-треда по первому `sendMessage` не возвращаем.
- `edit_bot_message` / исходящее медиа: `message_id` и байты считать по ленте `ChatRecord`, не по `bot_chats[(chat_id, bot_id)]`.
- `_press`: если `chat_id` в `chats` — искать сообщение в `ChatRecord`; `callback_query.message.chat` = группа/канал.

## 4. Модель

### ChatRecord

| Поле | Смысл |
|---|---|
| `id` | отрицательный, см. ниже |
| `type` | `group` \| `supergroup` \| `channel` |
| `title` | строка |
| `members` | `user_id → Member` |
| `messages` | лента `Message` |
| `last_bot_id` | кто из ботов последним успешно написал в этот чат; для голых `/cmd` |
| `privacy_at_join` | `bot_id → bool` (True = privacy включена в этом чате). Снимок в момент добавления |

### Id

Люди и боты — положительные, как сейчас.

- `group`: очередной `-1`, `-2`, … (не пересекается с пользователями).
- `supergroup` и `channel`: `int(f"-100{n:010d}")` при `n` с 1 → `-1000000000001`, … Аллокатор не выдаёт `-1001234567890` (занят e2e outbound). Если когда-нибудь дойдёт — пропустить.

Чужой отрицательный id без записи в `chats` и без outbound-пары — `400 chat not found`.

### Member

Внутри сети, не JSON: `status`, `user_id`, флаги админа, `until_date` для `kicked`, `promoted_by_bot_id: int | None` (кто из ботов сделал `promoteChatMember`; человек через User API → `None`).

Статусы из доки, без `restricted`: `creator`, `administrator`, `member`, `left`, `kicked`.

JSON — канонические объекты:

**ChatMemberOwner:** `status: "creator"`, `user`, `is_anonymous: false`.

**ChatMemberAdministrator** — обязательные поля всегда:

| Поле | |
|---|---|
| `status` | `"administrator"` |
| `user` | User |
| `can_be_edited` | считается в момент ответа: true, только если `promoted_by_bot_id == id вызывающего бота` |
| `is_anonymous` | `false` |
| `can_manage_chat` | |
| `can_delete_messages` | |
| `can_manage_video_chats` | |
| `can_restrict_members` | |
| `can_promote_members` | |
| `can_change_info` | |
| `can_invite_users` | |
| `can_post_stories` | |
| `can_edit_stories` | |
| `can_delete_stories` | |

Опциональные, только если тип чата тот:

| Поле | Где |
|---|---|
| `can_post_messages`, `can_edit_messages`, `can_manage_direct_messages` | `channel` |
| `can_pin_messages` | `group` и `supergroup` |
| `can_manage_topics` | `supergroup` |

`can_manage_tags` не отдаём (optional). Истории и кастомные title не делаем.

**ChatMemberMember:** `status: "member"`, `user`.

**ChatMemberLeft:** `status: "left"`, `user`.

**ChatMemberBanned:** `status: "kicked"`, `user`, `until_date` (`0` = навсегда).

Создатель чата — `creator`. Добавленный человек — `member` (в канале это подписчик). Бот в group/supergroup по умолчанию — `member`. Бот в канале **не бывает** `member`: сразу `administrator` с `can_post_messages: true`, остальные `can_*` false, кроме implied `can_manage_chat: true`.

После набора флагов: если любой `can_*` true → `can_manage_chat = true` (дока: implied by any other administrator privilege).

Дефолты (не путать add и demote):

| Действие | Результат |
|---|---|
| Add человека | `member` |
| Add бота в group/supergroup без `status` | `member` |
| Add бота в group/supergroup с `status: administrator` без `can_*` | `administrator`, все `can_*` false. Privacy: админ видит все сообщения людей. `promoteChatMember` от этого бота ещё нельзя (`can_promote_members` нет) |
| Add бота в channel | всегда `administrator`, `can_post_messages: true`, остальные `can_*` false, `can_manage_chat: true` |
| PATCH/promote на уже `administrator`, все переданные флаги false | demote, см. §5 |
| PATCH/promote `status: administrator` на `member` | становится админом; неуказанные `can_*` false; в канале если не передали `can_post_messages` — false (пост пропадёт, из канала не выгоняем) |
| `promoteChatMember` в канале, `can_restrict_members` не передан | `true` (дока, backward compatibility) |

«Все флаги false → demote» срабатывает **только** если цель уже `administrator` (creator — ошибка). Add с `status: administrator` без флагов — не demote.

Как считать «все false»:

- Bot API `promoteChatMember`: отсутствующий флаг = `false`, кроме канального `can_restrict_members` (если не передали — `true`). Demote — если **после** этих дефолтов все `can_*` false.
- User PATCH: опущенные `can_*` = `false`. Тот же demote-тест только если цель уже `administrator`. `{status: "administrator"}` на `member` demote не делает.

### Privacy на боте

На `BotRuntime`: `privacy_mode: bool = True` (включена, как BotFather). Смена: `POST /admin/bots/privacy`. `getMe.can_read_all_group_messages` = `not privacy_mode`. `getMe.can_join_groups` = true.

Смена настройки **не** переписывает `privacy_at_join` уже существующих чатов. Чтобы новое значение заработало в группе, бота надо выгнать и добавить снова — как у Telegram.

## 5. Потоки

### Создать и добавить

User API: смотрящий — `creator`. Admin: поле `creator_id`. Стартовых участников можно передать сразу.

- Человека добавляет любой, кто сейчас в чате (`member` и выше), в канале — только creator/administrator.
- Бота добавляет только creator/administrator.
- `POST …/members` тело: `{user_id, status?, can_*?}`. `status: "administrator"` разрешён, иначе смоук «бот-админ» не с чего начать. В канале бот всегда админ, см. таблицу дефолтов §4.
- `PATCH /user/chats/{id}/members/{user_id}`: `{status: "administrator"|"member", can_*?}`. Creator всегда может. Admin без сессии: те же пути; в `ChatMemberUpdated.from` — `creator` чата, либо явный `actor_id`, если передали.

Сервисное сообщение в ленте (канонический минимум, поле `text` не выдумываем):

```json
{
  "message_id": 1,
  "date": 1710000000,
  "chat": {"id": -1000000000001, "type": "supergroup", "title": "…"},
  "from": {"id": 1, "is_bot": false, "first_name": "А"},
  "new_chat_members": [{"id": 111111111, "is_bot": true, "first_name": "Club"}]
}
```

Выгон/выход — то же с `left_chat_member`: один `User`, не массив.

Любое изменение `Member.status` или прав админа:

- затронутому боту — `my_chat_member` (`ChatMemberUpdated`: `chat`, `from` = кто изменил, `date`, `old_chat_member`, `new_chat_member`);
- другим ботам-админам — `chat_member`, если тип в их `allowed_updates`.

Фильтр §6 применяется **после** смены статуса: выгнанный бот не получает `Update.message` с `left_chat_member`, только `my_chat_member`.

В **канале** сервисные `new_chat_members` / `left_chat_member` в ленту и в `Update.message` / `channel_post` не кладём: у ботов только `my_chat_member` / `chat_member`. Карточка канала в User API не врёт составом участников.

### Выгнать / выйти / бан

User API `DELETE …/members/{user_id}`:

- себя → `left` (`leave`);
- другого в `group` → `left` (из базовой группы можно вернуться добавлением);
- другого в `supergroup`/`channel` → `kicked` с `until_date: 0` (как Remove в клиенте Telegram).

Bot API `leaveChat` — бот → `left`. `banChatMember` — бот должен быть админом с `can_restrict_members`. Создателя банить нельзя: `400 Bad Request: can't remove chat owner`. В `group` `until_date` не применяется (дока: только super/channel).

В **basic group** `banChatMember` = kick: статус `left`. `unbanChatMember` на `group` — 400 `method is available only for supergroups and channels`. Человека снова добавляют через `POST …/members` без unban.

В **supergroup/channel** ban → `kicked` с `until_date: 0`. Без `unban` снова добавить нельзя. `unbanChatMember`: человек не возвращается сам; если он сейчас в чате и `only_if_banned` не true — его ещё и выкидывает в `left`. `unbanChatMember` по уже `kicked` → `status: left`, `until_date` сбросить, запись в `members` **не** удалять (дальше матрица §7: админ → 200 `ChatMemberLeft`).

### Promote / demote

Bot API `promoteChatMember` — только `supergroup` и `channel`. На `group`: `400 Bad Request: method is available only for supergroups and channels`. Вызывающий бот — админ с `can_promote_members`. User PATCH / добавление со `status: administrator` creator может всегда, в том числе на `group` (человек ставит админа в базовой группе; Bot API promote там по-прежнему 400).

Все флаги false → demote:

- человек в super/channel → `member`;
- бот в канале → `left` (в канале бот не бывает `member`), `my_chat_member`, из активных участников пропадает; последующий `sendMessage` — 403 `Forbidden: bot is not a member of the channel chat`, не «not enough rights»;
- бот в supergroup → `member`.

Creator demote/ban нельзя. Promote создателя: `400 Bad Request: can't promote the chat owner`.

### Письмо в группу

Человек пишет User API на отрицательный id. Нет в чате → 403. Сообщение в `ChatRecord.messages`, `from` = человек, `chat` = `{id, type, title}`. Опционально `reply_to_message_id` — в Message поле `reply_to_message`. SSE всем, у кого статус `creator`/`administrator`/`member`.

Ответ Bot API `sendMessage` / `edit*` в группу — тот же `chat`. В канал — без `from`, с `sender_chat` = канал (как дока). `edited_channel_post` уходит другим ботам канала, не автору правки.

Боту уходит `Update.message`, если проходит фильтр §6. `callback_query` с клавиатуры этого бота — всегда ему, privacy не режет.

ReplyKeyboardMarkup без `selective` в группе: записать клавиатуру каждому активному участнику, ключ `(member_id, chat_id)`.

### Канал

Пост только у creator или админа с `can_post_messages` (у creator право есть всегда). В JSON это `channel_post`: `sender_chat` = канал, поля `from` нет. Подписчики видят ленту в User API; композитор скрыт. Бот без права на `sendMessage` — 403 из таблицы §7.

Бот-участник канала получает чужие посты как `channel_post` **всегда** (privacy на каналы не действует). Свои исходящие через Bot API в очередь апдейтов не дублируем. `editMessageText` в канале даёт `edited_channel_post`, не `edited_message`.

### Лимиты

Профиль `telegram-faq` по умолчанию выключен. Когда включён:

- 1 сообщение/с в один чат (уже есть);
- ~30/с суммарно (уже есть);
- плюс 20 сообщений/мин в один `group`/`supergroup`/`channel` (исторический FAQ говорил «group»; каналы включаем так же, как копии FAQ / эмуляторы). Окно — 60 с на `(bot_id, chat_id)` только для записей в `Network.chats`. Private и outbound e2e (`-1001234567890`) под третий лимит не падают: иначе включённый профиль на инстансе убьёт алерты.

В актуальном FAQ текст про 20/мин уже не виден (страница про paid broadcasts). Число берём из зонтика и исторических копий FAQ.

## 6. Кому бот видит сообщение в группе

Порядок. Сообщения других ботов (`from.is_bot`) не доставляем никому — [FAQ](https://core.telegram.org/bots/faq).

Дальше, для каждого бота, у которого **после** события статус `creator`/`administrator`/`member`:

1. Сервисные (`new_chat_members`, `left_chat_member`) — всегда, если бот всё ещё в чате.
2. Бот-админ **или** `privacy_at_join[bot_id] is False` — все сообщения людей.
3. Иначе privacy включена. Бот видит только:
   - команду `/cmd@username` этого бота в **начале** текста (entity/prefix, не в середине);
   - голую `/cmd` в начале текста (без @), если `last_bot_id` равен этому боту;
   - реплай на сообщение с `from.id` этого бота. Реплай на «сообщение, адресованное боту» (цепочка не от бота) в волне 3 не делаем.

Свободный текст с @ботом **не** исключение. Inline-сообщения — волна 4.

Одно сообщение среди ботов **на шаге 3** (privacy включена, не админ) отдаём одному: реплай важнее команды на другого. Если реплай боту A содержит `/cmd@B` — из шага 3 видит только A. Шаг 2 не отменяется: бот-админ и бот с `privacy_at_join is False` это сообщение людей тоже получают.

`@username` бота без username в сети не матчится: у автосозданных ботов username уже есть (`tgmock{id}`).

## 7. Ошибки Bot API

Тело `{ok:false, error_code, description}`, HTTP = `error_code`.

| Ситуация | Ответ |
|---|---|
| Нет чата в сети (не private-человек, не outbound, не `chats`) | 400 `Bad Request: chat not found` |
| Private: человек есть, диалога не было | 403 `Forbidden: bot can't initiate conversation with a user` |
| Бот не в `group` / вышел | 403 `Forbidden: bot is not a member of the group chat` |
| то же, `supergroup` | 403 `Forbidden: bot is not a member of the supergroup chat` |
| то же, `channel` | 403 `Forbidden: bot is not a member of the channel chat` |
| Бот `kicked` из `group` | 403 `Forbidden: bot was kicked from the group chat` |
| из `supergroup` | 403 `Forbidden: bot was kicked from the supergroup chat` |
| из `channel` | 403 `Forbidden: bot was kicked from the channel chat` |
| Канал, бот админ, нет `can_post_messages` | 403 `Forbidden: not enough rights to send text messages to the chat` |
| `promote` / `unban` на `group` | 400 `Bad Request: method is available only for supergroups and channels` |
| `ban` / DELETE создателя | 400 `Bad Request: can't remove chat owner` |
| `promote` создателя | 400 `Bad Request: can't promote the chat owner` |
| Бан/unban без `can_restrict_members` | 403 `Forbidden: not enough rights to restrict/ban chat member` |
| Promote без `can_promote_members` | 400 `Bad Request: not enough rights` |
| `getChatMember`, человека в этом чате не было | 400 `Bad Request: user not found` |
| 20/мин (профиль включён, чат из `Network.chats`) | 429 `Too Many Requests: retry after N` с `parameters.retry_after` |

Порядок для бота, которого нет в чате или выгнали: `getChat` / `sendMessage` / `ban` / `promote` / … → сначала 403 not a member / was kicked (по типу чата).

`getChatMember` — правило **этой сети**, не дословная дока: все `users` видимы, private-диалог с ботом не требуется (иначе «добавили через User API → бот проверяет членство» сломается). У живого Telegram пользователь, которого бот «не видел», часто даёт 400.

- бота нет в `members` вовсе → 400 `user not found` (и для self);
- есть запись `left`/`kicked`: self → 200 `ChatMemberLeft`/`Banned`; caller админ → 200; caller не админ → 400 `user not found`;
- self в чате (`creator`/`administrator`/`member`) → 200;
- другой пользователь, caller админ → 200 для любой записи в `members`;
- другой пользователь, caller не админ → 200 только для `creator`/`administrator`/`member`; иначе 400 `user not found`.

`getChatMemberCount` — число `creator` + `administrator` + `member` (без `left`/`kicked`).

`getChatAdministrators`: creator и админы-люди; текущий бот, если он админ; чужие боты-админы — только при `return_bots=true`.

## 8. `allowed_updates`

Одна подписка на `BotRuntime`, общая для `getUpdates` и `setWebhook`.

- параметра нет и подписки ещё не было → дефолт: все типы, кроме `chat_member`, `message_reaction`, `message_reaction_count`;
- параметра нет, подписка уже есть → предыдущая;
- пустой список → всегда сброс на дефолт, даже если раньше был явный список;
- явный список → только он.

Фильтр на **выдаче** `getUpdates` / webhook, не на `push_update`. Уже лежащие апдейты при смене подписки не глотаем (дока: параметр не влияет на апдейты, созданные до вызова).

`my_chat_member` в дефолте есть. `chat_member` — нет, пока не попросили.

## 9. Фасады

### User API

Сессия как в волне 2 (`cookie` / `Bearer`).

| | Путь | Тело / смысл |
|---|---|---|
| POST | `/user/chats` | `{type, title, member_ids?, members?}` → `{chat}`. `type` ∈ `group\|supergroup\|channel`. Если есть `members` — он главный; `member_ids` без `members` = все `member` |
| GET | `/user/chats` | как было, плюс групповые, где смотрящий `creator`/`administrator`/`member`. У группы есть `title`, нет `first_name` |
| GET | `/user/chats/{id}/messages` | лента + `reply_keyboard` по правилу §2: private `(viewer, bot_id)`, группа `(viewer, chat_id)` |
| POST | `/user/chats/{id}/messages` | `{text, reply_to_message_id?}`; в канале — только с правом поста; не член → 403 |
| GET | `/user/chats/{id}/members` | `{members: [ChatMember]}` без `left`/`kicked`. Поле `can_be_edited` **опускаем** (это Bot API) |
| POST | `/user/chats/{id}/members` | `{user_id, status?, can_*?}` → `{member}` |
| PATCH | `/user/chats/{id}/members/{user_id}` | `{status, can_*?}` → `{member}` |
| DELETE | `/user/chats/{id}/members/{user_id}` | см. §5 |

Ошибки User API — JSON FastAPI, не маска Bot API. Тела не парсим в тестах по строке `description`; проверяем статус:

| HTTP | Когда |
|---|---|
| 401 | нет сессии |
| 400 | неизвестный чат / человек / невалидный `type` / PATCH создателя |
| 403 | нет в чате / нельзя добавить бота / нельзя постить в канал / нельзя PATCH чужие права |

### Admin

`POST /admin/chats` — `{type, title, creator_id, member_ids?, members?}`.

`POST /admin/chats/{id}/members` и `PATCH` / `DELETE` — зеркало User API без сессии.

Создание бота: необязательное `privacy` (дефолт `true` = включена). Смена: `POST /admin/bots/privacy` `{token, privacy: true|false}`.

### Bot API

Новые имена — в `IMPLEMENTED` и в журнал как `ok`, не как дыра.

Исходящий `sendMessage` в private **сменить** ключ reply-клавиатуры на `(user_id, bot_id)`. Сейчас код пишет `(chat_id, chat_id)` и читает `(viewer, viewer)` — это и есть баг волны 2.

## 10. Клиент

Формы «Группа» (шлёт `supergroup`) и «Канал» рядом с «Человек»/«Бот»: название, по желанию id участников через запятую. Дальше — User API, не Admin.

Список чатов и шапка: для группы/канала `title`, не `first_name` и не голый id.

Панель участников: имя, роль (`creator` / админ / участник), добавить из уже известных людей/ботов. При добавлении бота — чекбокс «админ» (`status: administrator`). Выгнать. Кнопок promote/ban нет: смена прав после добавления — PATCH из тестов/Admin.

Композитор `hidden`, если канал и у смотрящего нет права поста (creator всегда может). Сервисные сообщения в ленте видны (без выдуманного `text` — достаточно состава `new_chat_members` / факта ухода).

## 11. Хранение

В `dump`/`load` входят: `ChatRecord` (включая `privacy_at_join`, `last_bot_id`, `promoted_by_bot_id` у Member), `BotRuntime.privacy_mode`, `BotRuntime.allowed_updates` (после первого явного списка; отсутствие ключа = «ещё не задавали» — `getWebhookInfo.allowed_updates` тогда как в доке для дефолта, можно опустить поле). Снимок без ключа `chats` читается как `{}`. Подписчики SSE и сессии — как в волне 2, через `reset()`.

## 12. Тесты и гейты

Контракт в `tests/tgmock/`:

- id и `Chat.type`; private не сломан; POST в группу не создаёт `users[negative]`; `peer_id < 0` вне `chats` → 400 без `create_user`;
- неизвестный `chat_id` → 400 `chat not found`; человек без диалога → 403 `can't initiate` (два отдельных теста);
- outbound: после `ensure_outbound_chat` send на `-1001234567890` → 200 в `bot_chats`, не в `chats`; без пары чужой отрицательный → 400; алерты e2e живы;
- privacy: `/cmd@bot` в начале доходит; голый `/cmd` в начале — только последнему писавшему боту; `/cmd` в середине текста — нет; обычный текст — нет; `@mention` без `/cmd` не доходит; реплай (`reply_to_message_id`) на сообщение бота доходит; одно сообщение — одному privacy-боту; сервисные видит и privacy-бот, пока он в чате; бот-админ видит текст; `POST /admin/bots/privacy` без передобавления группу не меняет, после leave+add — меняет; `getMe.can_read_all_group_messages` / `can_join_groups`;
- чужой бот в ленту не попадает;
- `my_chat_member` при добавлении, promote, leave; `chat_member` только с `allowed_updates`; выгнанный не получает `message` про свой уход;
- канал: HTTP-результат `sendMessage` без `from`, с `sender_chat`; апдейт — `channel_post`; `edited_channel_post` не автору; demote бота → `left` и 403 not a member; add с чекбоксом админ в группу не demote-ит;
- User API POST в группу от non-member → 403; SSE `chat` = карточка группы;
- `promote` на `group` → 400; бан создателя → `can't remove chat owner`; promote создателя → `can't promote the chat owner`;
- `getChatAdministrators` без `return_bots` не отдаёт второго бота;
- `getChatMember`: left/kicked self 200, никогда не был 400;
- unban выкидывает живого; `only_if_banned` живого не трогает;
- `can_restrict_members` default true на channel promote;
- callback в группе: `message.chat.type` не private;
- 20/мин при включённом профиле на `ChatRecord`; private и outbound не бьёт;
- `allowed_updates`: omitted после явного списка сохраняет его; `[]` сбрасывает на дефолт; то же на `setWebhook` + `getWebhookInfo`;
- ban в `group` → человека снова добавляют без unban; в super/channel kicked без unban → POST members 403/400;
- клавиатура private видна в User API и `UserClient.screen()`, у двух ботов разная; в группе запись у каждого участника;
- dump/load: `chats`, `privacy_mode`, `allowed_updates`;

`UserClient.press` / `send` на private без изменений сигнатуры. `pytest -m e2e` зелёный. `make test-unit` зелёный. Порог покрытия боевых пакетов потребителя не меняется и эмулятор в себя не включает.

Смоук руками: создать супергруппу и канал, добавить человека и бота-участника, команда боту видна, болтовня нет; отметить «админ» / PATCH — болтовня видна. В канале подписчик не пишет, бот-админ пишет.

## 13. Критерий готовности

- Бот в группе получает апдейты по §6.
- Канал принимает `sendMessage` от бота с `can_post_messages` и отказывает без него.
- В клиенте создаются группа и канал, видны участники, бота можно сделать админом при добавлении.
- Профиль `telegram-faq`: 20/мин на чат из `Network.chats`.
- E2e потребителя зелёные. Outbound-алерты по-прежнему непрозрачный `chat_id`.
