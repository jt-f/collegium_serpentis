import React from 'react';
import { Zap, ShieldAlert, ShieldOff, AlertTriangle, Wifi, Clock, MessageSquare, MessageCircle, Loader2 } from 'lucide-react';

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

const formatAverageUptime = (onlineClients) => {
    if (onlineClients.length === 0) return 'N/A';

    // Parse uptime strings and convert to seconds for averaging
    let totalSeconds = 0;
    let validUptimes = 0;

    onlineClients.forEach(client => {
        const uptimeStr = client.uptime;
        if (uptimeStr && uptimeStr !== 'N/A') {
            // Parse uptime string like "1h 23m 45s" or "23m 45s" or "45s"
            const hours = uptimeStr.match(/(\d+)h/);
            const minutes = uptimeStr.match(/(\d+)m/);
            const seconds = uptimeStr.match(/(\d+)s/);

            let clientSeconds = 0;
            if (hours) clientSeconds += parseInt(hours[1]) * 3600;
            if (minutes) clientSeconds += parseInt(minutes[1]) * 60;
            if (seconds) clientSeconds += parseInt(seconds[1]);

            if (clientSeconds > 0) {
                totalSeconds += clientSeconds;
                validUptimes++;
            }
        }
    });

    if (validUptimes === 0) return 'N/A';

    const avgSeconds = Math.floor(totalSeconds / validUptimes);
    const hours = Math.floor(avgSeconds / 3600);
    const minutes = Math.floor((avgSeconds % 3600) / 60);
    const remainingSeconds = avgSeconds % 60;

    if (hours > 0) {
        return `${hours}h ${minutes}m`;
    } else if (minutes > 0) {
        return `${minutes}m ${remainingSeconds}s`;
    } else {
        return `${remainingSeconds}s`;
    }
};

const StatusOverview = ({ clients: clientsObj, isLoading, error, redisStatus, wsStatus }) => {
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

    let avgUptime = 'N/A';
    let totalMessagesSent = 0;
    let totalMessagesReceived = 0;

    if (onlineClients.length > 0) {
        avgUptime = formatAverageUptime(onlineClients);
        totalMessagesSent = onlineClients.reduce((acc, c) => acc + (parseInt(c.messages_sent) || 0), 0);
        totalMessagesReceived = onlineClients.reduce((acc, c) => acc + (parseInt(c.messages_received) || 0), 0);
    }

    return (
        <div className="bg-slate-800 p-6 rounded-xl shadow-xl">
            <div className="flex justify-between items-center mb-6">
                <h2 className="text-2xl font-semibold text-purple-400">System Status</h2>
                <div className="flex items-center space-x-2">
                    <span className={`text-xs px-2 py-1 rounded-full ${redisStatus === 'connected' ? 'bg-green-500/30 text-green-300' : 'bg-red-500/30 text-red-300'}`}>
                        Redis: {redisStatus}
                    </span>
                    <span className={`text-xs px-2 py-1 rounded-full 
                        ${wsStatus === 'connected' ? 'bg-sky-500/30 text-sky-300' :
                            wsStatus === 'connecting' ? 'bg-amber-500/30 text-amber-300' : 'bg-slate-600/30 text-slate-400'}`}>
                        Server: {wsStatus}
                    </span>
                </div>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
                <StatusCard title="Total Clients" value={totalClients} icon={<Wifi size={24} className="text-purple-400" />} colorClass="bg-slate-700" valueColor="text-purple-400" />
                <StatusCard title="Running" value={activeClients} icon={getStatusIcon('active')} valueColor='text-sky-400' />
                <StatusCard title="Disconnected" value={disconnectedClients} icon={getStatusIcon('inactive')} valueColor='text-slate-500' />
                <StatusCard title="Paused" value={pausedClients} icon={<AlertTriangle size={20} className="text-amber-500" />} valueColor='text-amber-400' />

                <StatusCard title="Avg. Uptime (Running)" value={avgUptime} icon={<Clock size={24} className="text-teal-400" />} valueColor='text-teal-400' />
                <StatusCard title="Total Messages Sent" value={totalMessagesSent} icon={<MessageSquare size={24} className="text-orange-400" />} valueColor='text-orange-400' />
                <StatusCard title="Total Messages Received" value={totalMessagesReceived} icon={<MessageCircle size={24} className="text-indigo-400" />} valueColor='text-indigo-400' />
            </div>
        </div>
    );
};

export default StatusOverview; 