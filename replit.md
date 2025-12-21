# Betfair Dutching Platform

## Overview

BetDutcher is a betting automation platform for Betfair Exchange Italy. It enables users to calculate and place dutching bets on exact score markets, providing real-time odds monitoring, stake calculations, and profit/loss tracking. The application connects to Betfair's API using SSL client certificates for secure authentication.

## User Preferences

Preferred communication style: Simple, everyday language.

**Release workflow**: Dopo ogni aggiornamento significativo completato, fornire istruzioni per push e release su GitHub con la versione successiva (comandi shell: git push origin main, git tag vX.X.X, git push origin vX.X.X).

**Current Version**: 3.9.3

## System Architecture

### Frontend Architecture
- **Framework**: React 18 with TypeScript
- **Routing**: Wouter (lightweight React router)
- **State Management**: TanStack Query for server state, React hooks for local state
- **UI Components**: shadcn/ui component library built on Radix UI primitives
- **Styling**: Tailwind CSS with CSS variables for theming (light/dark mode support)
- **Build Tool**: Vite with React plugin

### Backend Architecture
- **Runtime**: Node.js with Express
- **Language**: TypeScript compiled with tsx/esbuild
- **API Design**: RESTful endpoints under `/api/` prefix
- **Authentication**: Dual-mode authentication system
  - Replit Auth (OpenID Connect) for cloud deployment
  - Local SQLite-based auth for standalone/Electron builds

### Data Storage Solutions
- **Cloud Mode**: PostgreSQL with Drizzle ORM
- **Local/Electron Mode**: SQLite with better-sqlite3
- **Schema Definition**: Shared schema in `/shared/schema.ts` using Drizzle's schema builder

### Key Design Decisions

1. **Dual Database Strategy**: The app supports both PostgreSQL (cloud/Replit) and SQLite (local/Electron) to enable standalone desktop installation while maintaining cloud compatibility.

2. **Betfair SSL Certificate Authentication**: Uses mutual TLS with self-signed RSA 2048-bit certificates for Betfair API access. Certificate and private key are stored encrypted in the database.

3. **Monorepo Structure**: Client (`/client`), server (`/server`), and shared code (`/shared`) coexist with path aliases for clean imports.

4. **Electron Packaging Support**: Includes configuration for building Windows installers via electron-builder, bundling the server and client together.

5. **Component Architecture**: Uses shadcn/ui's copy-paste component model with Radix UI primitives for accessibility and customization.

## External Dependencies

### Betfair Exchange API
- **Purpose**: Real-time odds fetching, account balance, bet placement
- **Authentication**: SSL client certificate + session token
- **Endpoints**: Login API, Exchange API (markets, odds, betting)

### Database
- **PostgreSQL**: Primary database for cloud deployment (requires `DATABASE_URL`)
- **SQLite**: Embedded database for local/desktop mode (stored in `/data/betfair.db`)

### Authentication Services
- **Replit Auth**: OpenID Connect provider for cloud deployment
- **Session Storage**: PostgreSQL-backed sessions via connect-pg-simple

### Build & Packaging
- **Electron**: Desktop app wrapper for Windows distribution
- **electron-builder**: Creates NSIS installers for Windows

### Third-Party Libraries
- **Drizzle ORM**: Type-safe database queries and migrations
- **TanStack Query**: Server state management and caching
- **Zod**: Runtime validation for API requests and forms
- **react-hook-form**: Form state management with Zod integration