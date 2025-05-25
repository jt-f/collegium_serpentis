import React from 'react';
import { Zap, ShieldAlert, ShieldOff, AlertTriangle, Wifi, Cpu, MemoryStick, HardDrive } from 'lucide-react';

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

const StatusOverview = ({ clients }) => {
    const activeClients = clients.filter(c => c.status === 'active').length;
    const inactiveClients = clients.filter(c => c.status === 'inactive').length;
    const errorClients = clients.filter(c => c.status === 'error').length;
    const totalClients = clients.length;

    // Simplified resource aggregation (average or total for active clients)
    const avgCpu = clients.length > 0 ? `${(clients.reduce((acc, c) => acc + (parseFloat(c.cpu) || 0), 0) / activeClients).toFixed(0)}%` : 'N/A';
    const avgMemory = clients.length > 0 ? `${(clients.reduce((acc, c) => acc + (parseFloat(c.memory) || 0), 0) / activeClients).toFixed(0)}%` : 'N/A';
    const avgDisk = clients.length > 0 ? `${(clients.reduce((acc, c) => acc + (parseFloat(c.disk) || 0), 0) / activeClients).toFixed(0)}%` : 'N/A';


    return (
        <div className="bg-slate-800 p-6 rounded-xl shadow-xl">
            <h2 className="text-2xl font-semibold text-purple-400 mb-6">System Status</h2>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                <StatusCard title="Total Clients" value={totalClients} icon={<Wifi size={24} className="text-purple-400" />} colorClass="bg-slate-700" valueColor="text-purple-400" />
                <StatusCard title="Active" value={activeClients} icon={getStatusIcon('active')} valueColor='text-sky-400' />
                <StatusCard title="Inactive" value={inactiveClients} icon={getStatusIcon('inactive')} valueColor='text-slate-500' />
                <StatusCard title="Errors" value={errorClients} icon={getStatusIcon('error')} valueColor='text-red-400' />

                {/* Placeholder for aggregated resource usage, could be more detailed */}
                <StatusCard title="Avg. CPU" value={avgCpu} icon={<Cpu size={24} className="text-teal-400" />} valueColor='text-teal-400' />
                <StatusCard title="Avg. Memory" value={avgMemory} icon={<MemoryStick size={24} className="text-orange-400" />} valueColor='text-orange-400' />
                <StatusCard title="Avg. Disk" value={avgDisk} icon={<HardDrive size={24} className="text-indigo-400" />} valueColor='text-indigo-400' />

            </div>
        </div>
    );
};

export default StatusOverview; 