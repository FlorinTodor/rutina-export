#!/usr/bin/env bash
# Compila el APK. Busca un JDK con javac: el java del sistema suele ser solo
# el runtime y Gradle necesita el compilador.
set -euo pipefail
cd "$(dirname "$0")"

for cand in "${JAVA_HOME:-}" "$HOME/Android/jdk" /usr/lib/jvm/java-21-openjdk-amd64 \
            /usr/lib/jvm/default-java; do
    if [[ -n "$cand" && -x "$cand/bin/javac" ]]; then
        export JAVA_HOME="$cand"
        break
    fi
done
if [[ ! -x "${JAVA_HOME:-}/bin/javac" ]]; then
    echo "No encuentro un JDK con javac. Instala uno:  sudo apt install openjdk-21-jdk" >&2
    exit 1
fi
echo "JDK: $JAVA_HOME"

./gradlew "${@:-:app:assembleRelease}" --no-daemon
echo
echo "APK: $(pwd)/app/build/outputs/apk/release/app-release.apk"
