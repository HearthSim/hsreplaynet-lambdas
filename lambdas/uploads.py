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
import json
import logging
import os
import random
import boto3
import redis
import shortuuid


logger = logging.getLogger()
logger.setLevel(logging.INFO)


S3 = boto3.client("s3")


RAW_UPLOADS_BUCKET = os.getenv("RAW_UPLOADS_BUCKET", "hsreplaynet-uploads")
DESCRIPTOR_CACHE_HOST = os.getenv("DESCRIPTOR_CACHE_HOST")
DESCRIPTOR_CACHE_PORT = os.getenv("DESCRIPTOR_CACHE_PORT", 6379)
DESCRIPTOR_CACHE_DB = 0


PERCENT_CANARY_UPLOADS = 25


def is_canary_upload(event):
	if event and "query" in event and "canary" in event["query"]:
		return True

	dice_roll = random.randrange(0, 100)
	if dice_roll < PERCENT_CANARY_UPLOADS:
		return True

	return False


def get_timestamp():
	from datetime import datetime
	return datetime.now().strftime("%Y/%m/%d/%H/%M")


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


def save_descriptor_to_s3(descriptor, ts_path):
	s3_descriptor_key = "raw/%s/%s.descriptor.json" % (ts_path, descriptor["shortid"])

	S3.put_object(
		ACL="private",
		Key=s3_descriptor_key,
		Body=json.dumps(descriptor).encode("utf8"),
		Bucket=RAW_UPLOADS_BUCKET
	)


def save_descriptor_to_redis(descriptor):
	shortid = descriptor["shortid"]
	descriptor_body = json.dumps(descriptor)

	upload_descriptor_cache = redis.StrictRedis(
		host=DESCRIPTOR_CACHE_HOST,
		port=DESCRIPTOR_CACHE_PORT,
		db=DESCRIPTOR_CACHE_DB,
		socket_timeout=10
	)

	upload_descriptor_cache.set(shortid, descriptor_body)


def generate_log_upload_address_handler(event, context):
	gateway_headers = event["headers"]

	auth_token = get_auth_token(gateway_headers)
	shortid = get_shortid()

	ts_path = get_timestamp()

	body = base64.b64decode(event.pop("body"))
	upload_metadata = json.loads(body.decode("utf8"))

	if not isinstance(upload_metadata, dict):
		raise Exception("Meta data is not a valid JSON dictionary.")

	# A small fixed percentage of uploads are marked as canaries
	# 99% of the time they are handled identically to all other uploads
	# However during a lambdas deploy, the canaries are exposed to the newest code first
	# All canary uploads must succeed before the newest code gets promoted
	# To handle 100% of the upload volume.
	is_canary = is_canary_upload(event)
	if is_canary:
		upload_metadata["canary"] = is_canary

	log_template = "Token: %s | ShortID: %s | Canary: %s"
	logger.info(log_template % (auth_token, shortid, is_canary))

	descriptor = {
		"gateway_headers": gateway_headers,
		"shortid": shortid,
		"source_ip": event["source_ip"],
		"upload_metadata": upload_metadata,
		"event": event,
	}

	try:
		save_descriptor_to_redis(descriptor)
	except Exception as e:
		logger.exception("Could not connect to Redis. Using S3 instead.")
		save_descriptor_to_s3(descriptor, ts_path)

	# S3 only triggers downstream lambdas for PUTs suffixed with
	#  '...power.log' or '...canary.log'
	log_key_suffix = "power.log" if not is_canary else "canary.log"
	s3_powerlog_key = "raw/%s/%s.%s" % (ts_path, shortid, log_key_suffix)

	log_put_expiration = 60 * 60 * 24
	# Only one day, since if it hasn't been used by then it's unlikely to be used.
	presigned_put_url = S3.generate_presigned_url(
		"put_object",
		Params={
			"Bucket": RAW_UPLOADS_BUCKET,
			"Key": s3_powerlog_key,
			"ContentType": "text/plain",
		},
		ExpiresIn=log_put_expiration,
		HttpMethod="PUT"
	)

	return {
		"put_url": presigned_put_url,
		"shortid": shortid,
		"url": get_upload_url(shortid),
	}
