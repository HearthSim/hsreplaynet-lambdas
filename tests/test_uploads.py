import base64
import importlib
import os
import boto3
from moto import mock_s3
from lambdas import uploaders as uploads


def _mock_event_context():
	body = base64.b64encode(b"{}")
	event = {
		"headers": {
			"authorization": "Token Foo",
		},
		"body": body,
		"source_ip": "127.0.0.1",
	}
	context = None

	return event, context


@mock_s3
def test_uploads_lambda_bad_metadata():
	event, context = _mock_event_context()
	event["body"] = base64.b64encode(b"bad data")
	ret = uploads.generate_log_upload_address_handler(event, context)
	assert ret == {"error": "Invalid JSON"}


@mock_s3
def test_uploads_lambda_postgres():
	# set up the bucket
	s3 = boto3.client("s3")
	boto3.resource("s3").create_bucket(Bucket=uploads.RAW_UPLOADS_BUCKET)

	event, context = _mock_event_context()
	ret = uploads.generate_log_upload_address_handler(event, context)

	shortid = ret["shortid"]
	assert len(shortid) > 20
	assert ret["url"] == "https://hsreplay.net/uploads/upload/%s/" % (shortid)
	assert ret["put_url"].startswith("https://")

	# Check that the database has the shortid
	session = uploads.Session()
	instance = session.query(uploads.Descriptor).filter_by(shortid=shortid).one()
	assert instance.descriptor["event"] == event

	# Check that S3 is empty
	assert s3.list_objects_v2(Bucket=uploads.RAW_UPLOADS_BUCKET)["KeyCount"] == 0


@mock_s3
def test_uploads_lambda_s3():
	# set up the bucket
	s3 = boto3.client("s3")
	boto3.resource("s3").create_bucket(Bucket=uploads.DESCRIPTORS_BUCKET)
	boto3.resource("s3").create_bucket(Bucket=uploads.RAW_UPLOADS_BUCKET)

	# Prevent database from resolving
	os.environ["DB_HOST"] = "does-not-exist.invalid"
	importlib.reload(uploads)

	event, context = _mock_event_context()
	ret = uploads.generate_log_upload_address_handler(event, context)

	shortid = ret["shortid"]
	assert len(shortid) > 20
	assert ret["url"] == "https://hsreplay.net/uploads/upload/%s/" % (shortid)
	assert ret["put_url"].startswith("https://")

	# List the objects
	objs = s3.list_objects_v2(Bucket=uploads.DESCRIPTORS_BUCKET)["Contents"]
	assert len(objs) == 1

	assert shortid in objs[0]["Key"]
