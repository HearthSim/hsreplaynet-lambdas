"""
Minimalist Lambda Handlers

This module represents our most mission critical code and has minimal dependencies.

Specific design considerations:
- It is designed to not require DB connectivity
- It does not bootstrap the Django machinery
- It does not depend on hsreplaynet.* modules
- It makes minimal assumptions about the structure of the data it receives

These design considerations mean this lambda can be deployed on a different cycle than
the rest of the hsreplaynet codebase.
"""
import base64
import datetime
import json
import logging
import os
import random
import boto3
import shortuuid
from sqlalchemy import Column, create_engine, DateTime, VARCHAR
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine.url import URL
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm.session import sessionmaker


logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3 = boto3.client("s3")


LOG_PUT_EXPIRATION = 60 * 60 * 24
PERCENT_CANARY_UPLOADS = 25

RAW_UPLOADS_BUCKET = os.getenv("RAW_UPLOADS_BUCKET", "hsreplaynet-uploads")
DESCRIPTORS_BUCKET = os.getenv("DESCRIPTORS_BUCKET", "hsreplaynet-descriptors")

FORCE_S3 = os.getenv("FORCE_S3", "") == "1"

DB_USERNAME = os.getenv("DB_USERNAME", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "")
DB_PORT = os.getenv("DB_PORT", 5432)
DB_NAME = os.getenv("DB_NAME", "uploads")

DB_URL = URL(
	"postgresql",
	username=DB_USERNAME, password=DB_PASSWORD,
	host=DB_HOST, port=DB_PORT,
	database=DB_NAME
)
DB_ENGINE = create_engine(DB_URL, echo=True)
Session = sessionmaker(bind=DB_ENGINE)


class Descriptor(declarative_base()):
	__tablename__ = "uploads_descriptor"

	shortid = Column(VARCHAR(22), primary_key=True)
	descriptor = Column(postgresql.JSONB)
	created = Column(DateTime(timezone=True), default=datetime.datetime.utcnow)


def is_canary_upload(event):
	if event and "query" in event and "canary" in event["query"]:
		return True

	dice_roll = random.randrange(0, 100)
	if dice_roll < PERCENT_CANARY_UPLOADS:
		return True

	return False


def get_timestamp():
	return datetime.datetime.now().strftime("%Y/%m/%d/%H/%M")


def get_shortid():
	return shortuuid.uuid()


def get_upload_url(shortid):
	return "https://hsreplay.net/uploads/upload/%s/" % (shortid)


def get_auth_token(headers):
	lowercase_headers = {k.lower(): v for k, v in headers.items()}

	if "authorization" not in lowercase_headers:
		raise Exception("The Authorization Header is required.")

	auth_components = lowercase_headers["authorization"].split()
	if len(auth_components) != 2:
		raise Exception("Authorization header must have a scheme and a token.")

	return auth_components[1]


def save_descriptor_to_postgres(descriptor):
	session = Session()
	instance = Descriptor(shortid=descriptor["shortid"], descriptor=descriptor)
	session.add(instance)
	session.commit()


def save_descriptor_to_s3(descriptor):
	S3.put_object(
		ACL="private",
		Key="descriptors/%s.json" % (descriptor["shortid"]),
		Body=json.dumps(descriptor).encode("utf8"),
		Bucket=DESCRIPTORS_BUCKET
	)


def get_upload_metadata(event, is_canary):
	body = base64.b64decode(event.pop("body"))
	upload_metadata = json.loads(body.decode("utf8"))

	if not isinstance(upload_metadata, dict):
		raise Exception("Meta data is not a valid JSON dictionary.")

	# A small fixed percentage of uploads are marked as canaries
	# 99% of the time they are handled identically to all other uploads
	# However during a lambdas deploy, the canaries are exposed to the newest code first
	# All canary uploads must succeed before the newest code gets promoted
	# To handle 100% of the upload volume.
	if is_canary:
		upload_metadata["canary"] = is_canary

	return upload_metadata


def get_presigned_put_url(shortid, is_canary):
	ts_path = get_timestamp()

	# S3 only triggers downstream lambdas for PUTs suffixed with
	#  '...power.log' or '...canary.log'
	log_key_suffix = "power.log" if not is_canary else "canary.log"
	s3_powerlog_key = "raw/%s/%s.%s" % (ts_path, shortid, log_key_suffix)

	# Only one day, since if it hasn't been used by then it's unlikely to be used.
	return S3.generate_presigned_url(
		"put_object",
		Params={
			"Bucket": RAW_UPLOADS_BUCKET,
			"Key": s3_powerlog_key,
			"ContentType": "text/plain",
		},
		ExpiresIn=LOG_PUT_EXPIRATION,
		HttpMethod="PUT"
	)


def generate_log_upload_address_handler(event, context):
	auth_token = get_auth_token(event["headers"])
	shortid = get_shortid()
	is_canary = is_canary_upload(event)
	upload_metadata = get_upload_metadata(event, is_canary)
	logger.info("Token: %r, ID: %r, Canary %r", auth_token, shortid, is_canary)

	descriptor = {
		"shortid": shortid,
		"upload_metadata": upload_metadata,
		"event": event,
	}

	if FORCE_S3:
		save_descriptor_to_s3(descriptor)
	else:
		try:
			save_descriptor_to_postgres(descriptor)
		except Exception:
			logger.exception("Couldn't save to db")
			save_descriptor_to_s3(descriptor)

	presigned_put_url = get_presigned_put_url(shortid, is_canary)

	return {
		"put_url": presigned_put_url,
		"shortid": shortid,
		"url": get_upload_url(shortid),
	}
