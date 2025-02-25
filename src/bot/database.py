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

    async def add_to_db(
        self,
        chat_id,
        application_number,
        application_suffix,
        application_type,
        application_year,
        username=None,
        first_name=None,
        last_name=None,
        lang="EN",
    ):
        logger.info(f"Adding chatID {chat_id} with application number {application_number} to DB")
        query = (
            "INSERT INTO Applications "
            "(chat_id, application_number, application_suffix, application_type, application_year, current_status, "
            "username, first_name, last_name, language) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)"
        )
        params = (
            chat_id,
            application_number,
            application_suffix,
            application_type,
            application_year,
            "Unknown",
            username,
            first_name,
            last_name,
            lang,
        )
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, *params)
                return True
            except asyncpg.UniqueViolationError:
                logger.error(f"Attempt to insert duplicate chat ID {chat_id} and application number {application_number}")
                return False
            except Exception as e:
                logger.error(
                    f"Error while inserting into DB for chat ID: {chat_id} "
                    f"for application number {application_number}. Error: {e}"
                )
                return False

    async def update_db_status(self, chat_id, current_status, is_resolved):
        logger.info(f"Updating chatID {chat_id} current status in DB")
        query = "UPDATE Applications SET current_status = $1, last_updated = CURRENT_TIMESTAMP, is_resolved=$2 WHERE chat_id = $3"
        params = (current_status, is_resolved, chat_id)
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, *params)
                return True
            except Exception as e:
                logger.error(f"Error while updating DB for chat ID: {chat_id}. Error: {e}")
                return False

    async def remove_from_db(self, chat_id):
        logger.info(f"Removing chatID {chat_id} from DB")
        query = "DELETE FROM Applications WHERE chat_id = $1"
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, chat_id)
                return True
            except Exception as e:
                logger.error(f"Error while updating DB for chat ID: {chat_id}. Error: {e}")
                return False

    async def get_user_data_from_db(self, chat_id):
        """Fetch all user data for a given chat_id."""
        query = """
            SELECT * FROM Applications WHERE chat_id = $1;
        """
        async with self.pool.acquire() as conn:
            try:
                row = await conn.fetchrow(query, chat_id)
                if row is None:
                    logger.info(f"No data found for chat_id {chat_id}")
                    return None
                return dict(row)  # Convert the record to a dictionary
            except Exception as e:
                logger.error(f"Error while fetching user data for chat ID: {chat_id}. Error: {e}")
                return None

    async def get_application_status(self, chat_id):
        query = "SELECT current_status FROM Applications WHERE chat_id = $1;"
        async with self.pool.acquire() as conn:
            try:
                result = await conn.fetchval(query, chat_id)
                return result
            except Exception as e:
                logger.error(f"Error while fetching application status for chat ID: {chat_id}. Error: {e}")
                return None

    async def get_application_status_timestamp(self, chat_id, lang="EN"):
        query = "SELECT current_status, last_updated FROM Applications WHERE chat_id = $1;"
        async with self.pool.acquire() as conn:
            try:
                result = await conn.fetchrow(query, chat_id)
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
                logger.error(f"Error while fetching status from DB for chat ID: {chat_id}. Error: {e}")
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
        logger.info(f"Updating last_updated timestamp for chatID {chat_id} in DB")
        query = "UPDATE Applications SET last_updated = CURRENT_TIMESTAMP WHERE chat_id = $1"
        async with self.pool.acquire() as conn:
            try:
                await conn.execute(query, chat_id)
            except Exception as e:
                logger.error(f"Error while updating timestamp for chat ID: {chat_id}. Error: {e}")

    async def get_subscribed_user_count(self):
        """Return the count of unique users subscribed"""
        query = "SELECT COUNT(DISTINCT chat_id) FROM Applications;"
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
            # (olegeech) tmp to see how often we need DB to fetch language for each command
            logger.info(f"Going to DB to fetch language for user: {chat_id}")
            try:
                result = await conn.fetchval(query, chat_id)
                return result
            except Exception as e:
                logger.error(f"Error while fetching language for chat ID: {chat_id}. Error: {e}")
                return None

    async def set_user_language(self, chat_id, lang):
        logger.info(f"Updating chatID {chat_id} current status in DB")
        query = "UPDATE Applications SET language = $1 WHERE chat_id = $2"
        params = (lang, chat_id)
        logger.info(f"Update user {chat_id} language in DB to {lang}")
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
