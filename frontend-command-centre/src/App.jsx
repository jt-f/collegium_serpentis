import React, { useState, useEffect } from 'react';
import Layout from './components/Layout';
import ClientTable from './components/ClientTable';
import ChatWindow from './components/ChatWindow';
import StatusOverview from './components/StatusOverview';

function App() {
    const [clients, setClients] = useState({});
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState(null);
    const [redisStatus, setRedisStatus] = useState('unknown');

    useEffect(() => {
        const fetchClients = async () => {
            // Don't set isLoading to true on subsequent polls if data already exists
            if (Object.keys(clients).length === 0) {
                setIsLoading(true);
            }
            setError(null);
            try {
                const response = await fetch('/statuses');
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                const data = await response.json();
                setClients(data.clients || {});
                setRedisStatus(data.redis_status || 'unknown');
                if (data.error_redis) {
                    console.warn("Redis error from server:", data.error_redis);
                }
            } catch (e) {
                console.error("Failed to fetch client statuses:", e);
                setError(e.message);
                // Potentially setClients({}) here if preferred on error, 
                // or leave existing data
            } finally {
                // Only set isLoading to false if it was true (initial load)
                if (isLoading && Object.keys(clients).length === 0) {
                    setIsLoading(false);
                }
            }
        };

        fetchClients(); // Initial fetch
        const intervalId = setInterval(fetchClients, 5000); // Refresh every 5 seconds

        return () => clearInterval(intervalId); // Cleanup interval on component unmount
    }, []); // Empty dependency array means this effect runs once on mount and cleanup on unmount

    const messages = [
        { id: 1, sender: 'client-1', text: 'Obstacle detected at K2' },
        { id: 2, sender: 'Operator', text: 'Acknowledge Alpha Rover, rerouting.' },
        { id: 3, sender: 'client-3', text: 'Low battery warning.' },
    ];

    return (
        <Layout>
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 p-6 bg-slate-900 text-slate-50 min-h-screen">
                {/* Client Table and Status Overview in the first two columns for lg screens */}
                <div className="lg:col-span-2 flex flex-col gap-6">
                    <StatusOverview
                        clients={clients}
                        isLoading={isLoading && Object.keys(clients).length === 0}
                        error={error}
                        redisStatus={redisStatus}
                    />
                    <ClientTable
                        clients={clients}
                        isLoading={isLoading && Object.keys(clients).length === 0}
                        error={error}
                        redisStatus={redisStatus}
                    />
                </div>

                {/* Chat Window in the last column */}
                <div className="lg:col-span-1">
                    <ChatWindow messages={messages} />
                </div>
            </div>
        </Layout>
    );
}

export default App; 