#!/bin/bash
# Startup script for Railway deployment

echo "🚀 Starting Gavigans Multi-Agent Platform..."

# Convert DATABASE_URL from postgresql+asyncpg:// to postgresql:// for Prisma
# Railway uses postgresql+asyncpg:// but Prisma needs postgresql://
export DATABASE_URL=$(echo "$DATABASE_URL" | sed 's/postgresql+asyncpg/postgresql/g' | sed 's/ssl=require/sslmode=require/g')

echo "🔧 DATABASE_URL (for Prisma): ${DATABASE_URL:0:60}..."

# Run prisma db push to create/update tables
echo "🔧 Running prisma db push..."
python -m prisma db push --skip-generate --accept-data-loss

if [ $? -eq 0 ]; then
    echo "✅ Prisma tables ready"
else
    echo "⚠️ Prisma db push failed, but continuing..."
fi

# Run seed if tables are empty (first deployment)
echo "🌱 Checking if seed is needed..."
python -c "
import asyncio
from app.db import db

async def check_and_seed():
    await db.connect()
    user = await db.user.find_first()
    if not user:
        print('No users found - running seed...')
        from seed import seed
        await seed()
        print('✅ Seed completed')
    else:
        print('✅ Users exist - skipping seed')
    await db.disconnect()

asyncio.run(check_and_seed())
" 2>/dev/null || echo "⚠️ Seed check skipped"

# Start uvicorn
echo "🚀 Starting uvicorn..."
exec uvicorn main:app --host 0.0.0.0 --port $PORT
