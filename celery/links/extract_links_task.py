"""
Extract links/links from wikipedia indexed in mongodb by wtf_wikipedia.
Needs to parse redirects beforehand

The database will be created at datapath/{language}wiki_links.db

Couldn't send arguments to celery worker, therefore we need to change the language and data path manually here.
"""

import logging
from typing import List

import pymongo
from extract_links_worker import app
from pymongo import MongoClient

from celery import Task, bootsteps
from wbdsm.documents import Page
from wbdsm.links.extract_links import extract_links
from wbdsm.links.index_links import index_links

logger = logging.getLogger(__name__)


# https://docs.celeryq.dev/en/latest/userguide/tasks.html#instantiation
class IndexLinks(Task):
    abstract = True

    mongo_uri = "mongodb://localhost:27017"
    language = "de"

    # the cached requests.session object
    def __init__(self):
        #
        self.language = IndexLinks.language
        self.mongo_uri = IndexLinks.mongo_uri
        print(self.language)
        print(self.mongo_uri)
        db_name = self.language + "wiki"
        self.db_name = db_name
        client = MongoClient(self.mongo_uri)
        self.links_collection = client[db_name]["links"]
        self.pages_collection = client[db_name]["pages"]
        self.links_collection.create_index([("links_to", pymongo.HASHED)])
        self.links_collection.create_index([("source_doc", pymongo.HASHED)])
        self.links_collection.create_index([("text", pymongo.HASHED)])
        print("loaded")


# ! NOT WORKING
# https://stackoverflow.com/questions/27070485/initializing-a-worker-with-arguments-using-celery
# Make bootstep to add custom arguments


class CustomArgs(bootsteps.Step):
    def __init__(self, worker, mongo_uri, language, **options):
        super().__init__(worker, **options)
        print("Storing language and data_path")
        print("Language: ", language)
        print("Mongo URI: ", mongo_uri)
        IndexLinks.language = language[0]
        IndexLinks.mongo_uri = mongo_uri[0]

    def start(self, parent):
        # our step is started together with all other Worker/Consumer
        # bootsteps.
        print("{0!r} is starting".format(parent))


app.steps["worker"].add(CustomArgs)


# Could parse the article in different ways, like getting the text per paragraph, ignoring lists, depends on the objective. To match Zeshel's and mewsli's (uses wikiextractor) we will just append all the texts.
@app.task(
    name="extract_links_task",
    bind=True,
    queue="links_to_extract",
    base=IndexLinks,
    language=None,
    mongo_uri=None,
)
def extract_links_task(self, skip: str, limit: int, min_query_size: int = 50):
    """
    Celery task to extract links from wikipedia articles.
    The skip and limit parameters are used to paginate the query to the database.
    These parameters are controlled by the app in the extract_links_app.py

    Args:
        skip (str): Skip articles with id lower than this
        limit (int): Number of articles to parse
        min_query_size (int, optional): Min size of the query (in chars) to be considered. Defaults to 50. Values lower than this will be ignored.
    """
    # First
    logger.info("Getting articles from pages collection")
    # Get pages from pages collection
    pages = list(
        self.pages_collection.find({"isRedirect": False, "pageID": {"$gt": skip}})
        .sort("pageID", 1)
        .limit(limit)
    )
    # Transform in dataclasses to make it easier to work with and encapsulate parsing/cleaning logic
    pages_obj = [Page.from_mongo(page, self.language) for page in pages]
    links = extract_links(
        pages_obj, pages_collection=self.pages_collection, min_query_size=min_query_size
    )
    return links


# change to upsert https://stackoverflow.com/questions/30943076/mongoengine-bulk-update-without-objects-update
@app.task(
    base=IndexLinks,
    bind=True,
    queue="links_to_index",
    language=None,
    mongo_uri=None,
    name="index_links_task",
)
def index_links_task(self, links: List[dict]):
    """
    Index links in the database
    """
    index_links(links, self.links_collection)
