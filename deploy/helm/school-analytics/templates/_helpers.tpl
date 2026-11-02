{{- define "school-analytics.secretEnvName" -}}
{{- $normalized := regexReplaceAll "[^A-Za-z0-9]+" (upper .) "_" -}}
{{- printf "SA_SECRET_%s" $normalized -}}
{{- end -}}
