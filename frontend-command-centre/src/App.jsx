import React from 'react';
import Layout from './components/Layout';
import ClientTable from './components/ClientTable';
import ChatWindow from './components/ChatWindow';
import StatusOverview from './components/StatusOverview';

function App() {
    // Mock data - replace with actual data fetching and state management
    const clients = [
        { id: 'client-1', name: 'Alpha Rover', status: 'active', type: 'Rover', ip: '192.168.1.101', lastSeen: '2 mins ago', cpu: '35%', memory: '60%', disk: '40%' },
        { id: 'client-2', name: 'Bravo Drone', status: 'inactive', type: 'Drone', ip: '192.168.1.102', lastSeen: '1 hour ago', cpu: 'N/A', memory: 'N/A', disk: 'N/A' },
        { id: 'client-3', name: 'Charlie Sensor', status: 'error', type: 'Sensor', ip: '192.168.1.103', lastSeen: '5 mins ago', cpu: '15%', memory: '20%', disk: '10%' },
        { id: 'client-4', name: 'Delta Base', status: 'active', type: 'Base Station', ip: '192.168.1.100', lastSeen: '1 min ago', cpu: '50%', memory: '75%', disk: '60%' },
    ];

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
                    <StatusOverview clients={clients} />
                    <ClientTable clients={clients} />
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