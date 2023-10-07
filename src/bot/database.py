import asyncpg
import logging
import pytz
import asyncio
from bot.texts import message_texts

MAX_RETRIES = 5  # maximum number of connection retries
RETRY_DELAY = 2  # delay (in seconds) between retries

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, dbname, user, password, host, port, loop):
        self.dbname = dbname
        self.user = user
        self.password = password
        self.host = host
        self.port = port
        self.loop = loop
        self.pool = None

    async def connect(self, max_retries=MAX_RETRIES, delay=RETRY_DELAY):
        for attempt in range(1, max_retries + 1):
            try:
                self.pool = await asyncpg.create_pool(
                    database=self.dbname,
                    user=self.user,
                    password=self.password,
                    host=self.host,
                    port=self.port,
                    min_size=5,
                    max_size=20,
                )
                logger.info("Connected to the DB")
                break
            except Exception as e:
                logger.error(f"Failed to connect to the database. Attempt {attempt}/{max_retries}. Error: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(delay)
                    delay *= 2  # Double the delay for next retry
                else:
                    logger.error("Max retries reached. Unable to connect to the database")
                    raise

    async def add_user_to_db(self, chat_id, first_name, username=None, last_name=None, lang="EN"):
        logger.info(f"Adding user with chatID {chat_id} to DB")
        query = "INSERT INTO Users " "(chat_id, username, first_name, last_name, language) " "VALUES ($1, $2, $3, $4, $5)"
        params = (chat_id, username, first_name, last_name, lang)
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, *params)
            except asyncpg.UniqueViolationError:
                logger.error(f"Attempt to insert duplicate user, chat ID {chat_id}")
                return False
            except Exception as e:
                logger.error(f"Error while inserting into Users table for chat ID: {chat_id}. Error: {e}")
                return False
        return True

    async def add_application_to_db(
        self,
        chat_id,
        application_number,
        application_suffix,
        application_type,
        application_year,
    ):
        logger.info(f"Adding application for chatID {chat_id} to DB")
        query = (
            "INSERT INTO Applications "
            "(user_id, application_number, application_suffix, application_type, application_year) "
            "SELECT user_id, $2, $3, $4, $5 FROM Users WHERE chat_id = $1"
        )
        params = (chat_id, application_number, application_suffix, application_type, application_year)
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, *params)
            except asyncpg.UniqueViolationError:
                logger.error(f"Attempt to insert duplicate application for user {chat_id} and number {application_number}")
                return False
            except Exception as e:
                logger.error(
                    f"Error while inserting into Applications table for user {chat_id}, number: {application_number}. Error: {e}"
                )
                return False
        return True

    async def count_user_subscriptions(self, chat_id):
        query = "SELECT COUNT(*) FROM Applications WHERE user_id = (SELECT user_id FROM Users WHERE chat_id = $1)"
        async with self.pool.acquire() as conn:
            try:
                count = await conn.fetchval(query, chat_id)
                return count
            except Exception as e:
                logger.error(f"Error while fetching subscription count for chat ID: {chat_id}. Error: {e}")
                return None

    async def update_db_status(self, chat_id, application_number, current_status, is_resolved):
        query = """UPDATE Applications
                   SET current_status = $1, last_updated = CURRENT_TIMESTAMP, is_resolved=$2
                   WHERE user_id = (SELECT user_id FROM Users WHERE chat_id = $3)
                   AND application_number = $4"""
        params = (current_status, is_resolved, chat_id, application_number)
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, *params)
                return True
            except Exception as e:
                logger.error(
                    f"Error while updating DB for chat ID: {chat_id} and application number: {application_number}. Error: {e}"
                )
                return False

    async def remove_from_db(self, chat_id, application_number):
        query = """DELETE FROM Applications
                   WHERE user_id = (SELECT user_id FROM Users WHERE chat_id = $1)
                   AND application_number = $2"""
        params = (chat_id, application_number)
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, *params)
                return True
            except Exception as e:
                logger.error(
                    f"Error while removing from DB for user {chat_id} and application number: {application_number}. Error: {e}"
                )
                return False

    async def get_user_data_from_db(self, chat_id):
        query = """SELECT *
                   FROM Applications
                   WHERE user_id = (SELECT user_id FROM Users WHERE chat_id = $1)"""
        async with self.pool.acquire() as conn:
            try:
                rows = await conn.fetch(query, chat_id)
                if not rows:
                    logger.info(f"No data found for chat_id {chat_id}")
                    return None
                return [dict(row) for row in rows]  # Convert the records to dictionaries
            except Exception as e:
                logger.error(f"Error while fetching user data for chat ID: {chat_id}. Error: {e}")
                return None

    async def get_application_status(self, chat_id, application_number):
        """Fetch status for a specific application for a given chat_id."""
        query = """SELECT current_status
                   FROM Applications
                   WHERE user_id = (SELECT user_id FROM Users WHERE chat_id = $1)
                   AND application_number = $2"""
        params = (chat_id, application_number)

        async with self.pool.acquire() as conn:
            try:
                result = await conn.fetchval(query, *params)
                return result
            except Exception as e:
                logger.error(
                    f"Error while fetching application status for user {chat_id} and number: {application_number}. Error: {e}"
                )
                return None

    async def get_application_status_timestamp(self, chat_id, application_number, lang="EN"):
        """Fetch status and timestamp for a specific application for a given chat_id."""
        query = """SELECT current_status, last_updated
                   FROM Applications
                   WHERE user_id = (SELECT user_id FROM Users WHERE chat_id = $1)
                   AND application_number = $2"""
        params = (chat_id, application_number)

        async with self.pool.acquire() as conn:
            try:
                result = await conn.fetchrow(query, *params)
                if result is not None and result["last_updated"]:
                    current_status = result["current_status"]
                    last_updated_utc = result["last_updated"].replace(tzinfo=pytz.utc)
                    last_updated_prague = last_updated_utc.astimezone(pytz.timezone("Europe/Prague"))
                    timestamp = last_updated_prague.strftime("%H:%M:%S %d-%m-%Y")

                    status_str = message_texts[lang]["current_status_timestamp"].format(
                        status=current_status,
                        timestamp=timestamp,
                    )
                    return status_str
                else:
                    return message_texts[lang]["current_status_empty"]
            except Exception as e:
                logger.error(
                    f"Error while fetching status from DB for {chat_id} and application number: {application_number}. Error: {e}"
                )
                return message_texts[lang]["error_generic"]

    async def check_subscription_in_db(self, chat_id):
        query = "SELECT EXISTS(SELECT chat_id FROM Applications WHERE chat_id=$1)"
        async with self.pool.acquire() as conn:
            try:
                result = await conn.fetchval(query, chat_id)
                return result
            except Exception as e:
                logger.error(f"Error while checking chat_id {chat_id} subscription. Error: {e}")
                return False

    async def get_applications_needing_update(self, refresh_period):
        # Convert the timedelta refresh period to seconds for the SQL interval.
        seconds = refresh_period.total_seconds()

        # Fetch rows where the current time minus last_checked is more than the refresh period.
        query = """
            SELECT chat_id, application_number, application_suffix, application_type, application_year, last_updated
            FROM Applications
            WHERE EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - COALESCE(last_updated, TIMESTAMP '1970-01-01'))) > $1
            AND is_resolved = FALSE
        """

        async with self.pool.acquire() as conn:
            try:
                return await conn.fetch(query, seconds)
            except Exception as e:
                logger.error(f"Error while fetching applications needing update. Error: {e}")
                return []

    async def update_timestamp(self, chat_id):
        logger.debug(f"Updating last_updated timestamp for chatID {chat_id} in DB")
        query = "UPDATE Applications SET last_updated = CURRENT_TIMESTAMP WHERE chat_id = $1"
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, chat_id)
            except Exception as e:
                logger.error(f"Error while updating timestamp for chat ID: {chat_id}. Error: {e}")

    async def check_user_exists_in_db(self, chat_id):
        """Check if a user exists in the database"""
        query = "SELECT EXISTS(SELECT 1 FROM Users WHERE chat_id = $1)"
        async with self.pool.acquire() as conn:
            try:
                exists = await conn.fetchval(query, chat_id)
                return exists
            except Exception as e:
                logger.error(f"Error while checking if user with chat_id {chat_id} exists. Error: {e}")
                return False

    async def get_user_subscriptions(self, chat_id):
        """Fetch all application numbers for a given chat_id"""
        query = """SELECT application_number
                   FROM Applications
                   WHERE user_id = (SELECT user_id FROM Users WHERE chat_id = $1)"""
        async with self.pool.acquire() as conn:
            try:
                rows = await conn.fetch(query, chat_id)
                # Extract application numbers from the rows and return as a list
                return [row["application_number"] for row in rows]
            except Exception as e:
                logger.error(f"Error while fetching application numbers for chat ID: {chat_id}. Error: {e}")
                return []

    async def get_subscribed_user_count(self):
        query = "SELECT COUNT(*) FROM Users"
        async with self.pool.acquire() as conn:
            try:
                count = await conn.fetchval(query)
                return count
            except Exception as e:
                logger.error(f"Error while fetching subscribed user count. Error: {e}")
                return None

    async def get_user_language(self, chat_id):
        query = "SELECT language FROM Applications WHERE chat_id = $1;"
        async with self.pool.acquire() as conn:
            logger.debug(f"Going to DB to fetch language for user: {chat_id}")
            try:
                result = await conn.fetchval(query, chat_id)
                return result
            except Exception as e:
                logger.error(f"Error while fetching language for chat ID: {chat_id}. Error: {e}")
                return None

    async def set_user_language(self, chat_id, lang):
        logger.debug(f"Update user {chat_id} language in DB to {lang}")
        query = "UPDATE Applications SET language = $1 WHERE chat_id = $2"
        params = (lang, chat_id)
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, *params)
                return True
            except Exception as e:
                logger.error(f"Error while updating lang in DB for chat ID: {chat_id}. Error: {e}")
                return False

    async def close(self):
        logger.info("Shutting down DB connection")
        try:
            await self.pool.close()
        except Exception as e:
            logger.error(f"Error while shutting down DB connection. Error: {e}")
