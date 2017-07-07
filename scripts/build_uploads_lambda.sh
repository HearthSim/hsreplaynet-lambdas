BASEDIR=$(readlink -f "$(dirname $0)/..")
ZIPFILE="$1"
SITE_PACKAGES=$(python -c "import sys; print(sys.path[-1])")

ZIPFILE="$1"
if [[ -z $ZIPFILE ]]; then
	>&2 echo "Usage: $0 <ZIPFILE>"
	exit 1
fi
ZIPFILE=$(readlink -f "$ZIPFILE")

SITE_PACKAGES=$(python -c "import sys; print(sys.path[-1])")
echo "site-packages is $SITE_PACKAGES"
echo "Archiving to $ZIPFILE"

rm -f "$ZIPFILE"

cd "$BASEDIR/lambdas"
zip -r "$ZIPFILE" "uploaders.py" "__init__.py"


# Install and package dependencies
pip install shortuuid psycopg2 sqlalchemy

cd "$SITE_PACKAGES"
zip -r "$ZIPFILE" "./shortuuid/" "./psycopg2/" "./sqlalchemy/"


echo "Written to $ZIPFILE"
