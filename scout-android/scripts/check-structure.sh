#!/usr/bin/env bash
set -Eeuo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

required_files=(
  "app/src/main/AndroidManifest.xml"
  "app/src/main/java/ai/relicscope/scout/MainActivity.kt"
  "app/src/main/java/ai/relicscope/scout/ScoutApplication.kt"
  "app/src/main/java/ai/relicscope/scout/quality/ImageQuality.kt"
  "app/src/main/java/ai/relicscope/scout/data/ScoutDatabase.kt"
  "app/src/main/java/ai/relicscope/scout/work/UploadScoutJobWorker.kt"
  "app/src/main/java/ai/relicscope/scout/work/PollScoutJobWorker.kt"
  "app/src/main/java/ai/relicscope/scout/work/RetryScoutJobWorker.kt"
  "app/src/main/java/ai/relicscope/scout/security/SecureDeviceConfig.kt"
  "app/src/main/res/xml/network_security_config.xml"
  "gradle/wrapper/gradle-wrapper.jar"
)

for relative_path in "${required_files[@]}"; do
  test -s "${project_root}/${relative_path}" || {
    printf 'missing required file: %s\n' "${relative_path}" >&2
    exit 1
  }
done

while IFS= read -r xml_file; do
  xmllint --noout "${xml_file}"
done < <(find "${project_root}/app/src" -type f -name '*.xml' -print | sort)

unzip -tqq "${project_root}/gradle/wrapper/gradle-wrapper.jar"
shasum -a 256 "${project_root}/gradle/wrapper/gradle-wrapper.jar" \
  | grep -q '^55243ef57851f12b070ad14f7f5bb8302daceeebc5bce5ece5fa6edb23e1145c  '
grep -q '^distributionSha256Sum=2ab2958f2a1e51120c326cad6f385153bb11ee93b3c216c5fccebfdfbb7ec6cb$' \
  "${project_root}/gradle/wrapper/gradle-wrapper.properties"
grep -q 'android:usesCleartextTraffic="false"' "${project_root}/app/src/main/AndroidManifest.xml"
grep -q 'cleartextTrafficPermitted="false"' "${project_root}/app/src/main/res/xml/network_security_config.xml"
grep -q '<debug-overrides>' "${project_root}/app/src/main/res/xml/network_security_config.xml"
grep -q 'certificates src="user"' "${project_root}/app/src/main/res/xml/network_security_config.xml"
grep -q 'api/v2/scout/jobs/.*retry' "${project_root}/app/src/main/java/ai/relicscope/scout/network/ScoutApiClient.kt"
if grep -R -E 'http://|Bearer [A-Za-z0-9._-]{12,}' "${project_root}/app/src/main/java"; then
  printf 'possible cleartext URL or embedded bearer token found\n' >&2
  exit 1
fi

printf 'Scout Android structural checks passed.\n'
