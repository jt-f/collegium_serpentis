import React from 'react';
import { Zap, ShieldAlert, ShieldOff, AlertTriangle, Wifi, Cpu, MemoryStick, HardDrive, Loader2 } from 'lucide-react';

const StatusCard = ({ title, value, icon, colorClass = 'bg-slate-700', valueColor = 'text-sky-400' }) => (
    <div className={`p-4 rounded-lg shadow-md flex items-center space-x-3 ${colorClass}`}>
        <div className={`p-2 rounded-full ${valueColor === 'text-sky-400' ? 'bg-sky-500/20' : valueColor === 'text-red-400' ? 'bg-red-500/20' : valueColor === 'text-amber-400' ? 'bg-amber-500/20' : 'bg-slate-600'}`}>
            {icon}
        </div>
        <div>
            <p className="text-sm text-slate-400">{title}</p>
            <p className={`text-xl font-semibold ${valueColor}`}>{value}</p>
        </div>
    </div>
);

const getStatusIcon = (status) => {
    switch (status) {
        case 'active': return <Zap size={20} className="text-sky-400" />;
        case 'inactive': return <ShieldOff size={20} className="text-slate-500" />;
        case 'error': return <ShieldAlert size={20} className="text-red-500" />;
        default: return <AlertTriangle size={20} className="text-amber-500" />;
    }
};

const StatusOverview = ({ clients: clientsObj, isLoading, error, redisStatus }) => {
    if (isLoading) {
        return (
            <div className="bg-slate-800 p-6 rounded-xl shadow-xl flex items-center justify-center min-h-[200px]">
                <Loader2 className="animate-spin text-purple-400" size={32} />
                <p className="ml-3 text-slate-400">Loading system status...</p>
            </div>
        );
    }

    if (error) {
        return (
            <div className="bg-slate-800 p-6 rounded-xl shadow-xl">
                <h2 className="text-2xl font-semibold text-red-400 mb-6">System Status Error</h2>
                <p className="text-center text-red-400 py-4">Could not load system status: {error}</p>
                <p className="text-center text-xs text-slate-500">Redis: {redisStatus}</p>
            </div>
        );
    }

    const clientList = Object.values(clientsObj || {});

    const activeClients = clientList.filter(c => c.connected === 'true' && c.client_state === 'running').length;
    const pausedClients = clientList.filter(c => c.connected === 'true' && c.client_state === 'paused').length;
    const disconnectedClients = clientList.filter(c => c.connected !== 'true').length;
    const errorClients = clientList.filter(c => c.client_state === 'error' || c.status_detail?.toLowerCase().includes('error')).length;
    const totalClients = clientList.length;

    const onlineClients = clientList.filter(c => c.connected === 'true' && c.client_state === 'running');

    let avgCpu = 'N/A';
    let avgMemory = 'N/A';
    let avgDisk = 'N/A';

    if (onlineClients.length > 0) {
        avgCpu = `${(onlineClients.reduce((acc, c) => acc + (parseFloat(c.cpu_usage) || 0), 0) / onlineClients.length).toFixed(0)}%`;
        avgMemory = `${(onlineClients.reduce((acc, c) => acc + (parseFloat(c.memory_usage) || 0), 0) / onlineClients.length).toFixed(0)}%`;
        avgDisk = `${(onlineClients.reduce((acc, c) => acc + (parseFloat(c.disk_usage) || 0), 0) / onlineClients.length).toFixed(0)}%`;
    }

    return (
        <div className="bg-slate-800 p-6 rounded-xl shadow-xl">
            <div className="flex justify-between items-center mb-6">
                <h2 className="text-2xl font-semibold text-purple-400">System Status</h2>
                <span className={`text-xs px-2 py-1 rounded-full ${redisStatus === 'connected' ? 'bg-green-500/30 text-green-300' : 'bg-red-500/30 text-red-300'}`}>
                    Redis: {redisStatus}
                </span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                <StatusCard title="Total Clients" value={totalClients} icon={<Wifi size={24} className="text-purple-400" />} colorClass="bg-slate-700" valueColor="text-purple-400" />
                <StatusCard title="Running" value={activeClients} icon={getStatusIcon('active')} valueColor='text-sky-400' />
                <StatusCard title="Disconnected" value={disconnectedClients} icon={getStatusIcon('inactive')} valueColor='text-slate-500' />
                <StatusCard title="Paused" value={pausedClients} icon={<AlertTriangle size={20} className="text-amber-500" />} valueColor='text-amber-400' />

                <StatusCard title="Avg. CPU (Running)" value={avgCpu} icon={<Cpu size={24} className="text-teal-400" />} valueColor='text-teal-400' />
                <StatusCard title="Avg. Memory (Running)" value={avgMemory} icon={<MemoryStick size={24} className="text-orange-400" />} valueColor='text-orange-400' />
                <StatusCard title="Avg. Disk (Running)" value={avgDisk} icon={<HardDrive size={24} className="text-indigo-400" />} valueColor='text-indigo-400' />
            </div>
        </div>
    );
};

export default StatusOverview; 