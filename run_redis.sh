#!/bin/bash
docker run --rm --name redis-server -p 6379:6379 -p 8001:8001 redis/redis-stack