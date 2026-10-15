{{- define "watchtower.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}{{ .Release.Name | trunc 50 | trimSuffix "-" }}
{{- else -}}{{ printf "%s-%s" .Release.Name .Chart.Name | trunc 50 | trimSuffix "-" }}{{- end -}}
{{- end -}}

{{- define "watchtower.labels" -}}
app.kubernetes.io/part-of: watchtower
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "watchtower.selector" -}}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/name: {{ .component }}
{{- end -}}

{{- define "watchtower.image" -}}
{{ .root.Values.image.registry }}/{{ .root.Values.image.owner }}/watchtower-{{ .service }}:{{ .root.Values.image.tag | default .root.Chart.AppVersion }}
{{- end -}}

{{- define "watchtower.env" -}}
{{- range $name, $value := . }}
- name: {{ $name }}
  value: {{ $value | quote }}
{{- end }}
{{- end -}}

{{- define "watchtower.podSecurity" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "watchtower.containerSecurity" -}}
allowPrivilegeEscalation: false
capabilities:
  drop: [ALL]
{{- end -}}
