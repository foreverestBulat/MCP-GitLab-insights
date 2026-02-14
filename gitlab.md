```
docker run -d -p 8081:80 -v ${PWD}/gitlab-openapi.yaml:/usr/share/nginx/html/openapi.yaml -e SPEC_URL=/usr/share/nginx/html/openapi.yaml redocly/redoc 
```