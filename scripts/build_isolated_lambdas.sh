BASEDIR=$(pwd)
ZIPFILE="$BASEDIR/isolated_lambdas.zip"
SITE_PACKAGES=$(python -c "import sys; print(sys.path[-1])")

rm -f "$ZIPFILE"

cd "../../isolated"
zip -r "$ZIPFILE" ./*

cd "$SITE_PACKAGES"
zip -r "$ZIPFILE" "./shortuuid/"

echo "Written to $ZIPFILE"
