#!/bin/sh
# Entry point of the OmicChain image.
#   app (default)        start the application in /work (http://localhost:8501 on the host)
#   verify <arguments>   run omicchain_verify.py in /work (crate paths are relative to /work)
#   test [group]         run the test suite of the image (default group: real)
#   anything else        run that command
set -e
SRC=/opt/omicchain

sync_code() {
    # The application runs from the project folder: its code is copied there at each start
    # (the user's files are never removed; only the files of OmicChain are replaced).
    (cd "$SRC" && tar cf - --exclude=./tests --exclude=./.github --exclude=./docker --exclude=./portal \
                         --exclude=./Dockerfile --exclude=./docker-compose.yml .) | tar xf - -C /work --no-same-owner
}

case "${1:-app}" in
    app)
        sync_code
        cd /work
        echo "OmicChain is starting: open http://localhost:8501 in your browser."
        exec streamlit run OmicChain.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true
        ;;
    verify)
        shift
        sync_code
        cd /work
        exec python omicchain_verify.py "$@"
        ;;
    test)
        shift
        cd "$SRC"
        export OMICCHAIN_REQUIRE_REAL="${OMICCHAIN_REQUIRE_REAL:-1}"
        exec python tests/run_all.py "${1:-real}"
        ;;
    *)
        exec "$@"
        ;;
esac
