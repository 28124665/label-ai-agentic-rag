{{/*
Expand the name of the chart.
*/}}
{{- define "data-knowledge-api.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "data-knowledge-api.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "data-knowledge-api.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "data-knowledge-api.labels" -}}
helm.sh/chart: {{ include "data-knowledge-api.chart" . }}
{{ include "data-knowledge-api.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Resolve image repository with optional global repo prefix.
If .Values.global.repo is set, replace registry part and keep image path.
Detect existing registry by first segment containing '.' or ':' or being 'localhost'.
Usage: {{ include "data-knowledge-api.imageRepo" (dict "root" . "repo" .Values.foo.image.repository) }}
*/}}
{{- define "data-knowledge-api.imageRepo" -}}
{{- $root := .root -}}
{{- $repo := .repo -}}
{{- $global := $root.Values.global -}}
{{- if and $global $global.repo }}
  {{- $parts := splitList "/" $repo -}}
  {{- $first := index $parts 0 -}}
  {{- $hasRegistry := or (regexMatch "\\." $first) (regexMatch ":" $first) (eq $first "localhost") -}}
  {{- if $hasRegistry -}}
    {{- $path := join "/" (rest $parts) -}}
    {{- printf "%s/%s" $global.repo $path -}}
  {{- else -}}
    {{- printf "%s/%s" $global.repo $repo -}}
  {{- end -}}
{{- else -}}
  {{- $repo -}}
{{- end -}}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "data-knowledge-api.selectorLabels" -}}
app.kubernetes.io/name: {{ include "data-knowledge-api.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "data-knowledge-api.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "data-knowledge-api.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}
